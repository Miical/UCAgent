"""Measure bounded, project-scoped rounds of coverage-directed testing."""

import hashlib
import json
from pathlib import Path
import re
import time
from typing import Annotated, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from ucagent.checkers.base import Checker

from .coverage_runtime import public as coverage_state
from .regression_workflow import (
    DIAGNOSES, FINDINGS, analyze, batch_results, publish_findings, regression_scope,
)
from .targets import Targets, atomic
from .testcases import Cases


STATE = 'coverage-improvement.json'
PAGE_SIZE = 30
Percent = Annotated[float, Field(ge=0, le=100)]


class Options(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_rounds: int = Field(default=1, ge=1, le=20)
    functional_target: Percent | None = None
    line_target: Percent | None = None
    time_budget_minutes: float | None = Field(default=None, gt=0, le=1440)


class SourceLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    instance: str
    file: str
    line: int = Field(ge=1)


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    case_id: str
    strategy: Literal['directed', 'random']
    target_ids: list[str] = Field(default_factory=list)
    lines: list[SourceLine] = Field(default_factory=list)
    seeds: list[Annotated[int, Field(ge=0, le=2**64-1, strict=True)]] = Field(default_factory=list, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def require_a_gap_and_replayable_seeds(self):
        if not self.target_ids and not self.lines:
            raise ValueError('每条补测用例至少需要一个覆盖缺口')
        if (self.strategy == 'random') != bool(self.seeds) or len(set(self.seeds)) != len(self.seeds):
            raise ValueError('随机用例必须提供不重复的 seed，定向用例无需 seed')
        return self


PROPOSALS = TypeAdapter(list[Proposal])


def read_state(path):
    return json.loads(path.read_text()) if path.is_file() else None


def queued_batch(root):
    """Expose only checker-accepted batches to the FlowCanvas scheduler."""
    state = read_state(root / STATE)
    return state.get('pending') if state else None


def line_key(instance, file, line):
    return json.dumps([instance, file, int(line)], ensure_ascii=False)


def coverage_snapshot(project):
    """Measure the published build and only current, source-backed coverage points."""
    value = coverage_state(project)
    report = value.get('report') or {}
    lines, targets = {}, {}
    hashes = {}
    for instance in report.get('instances', []):
        for file in instance.get('files', []):
            name = file['path']
            if name not in hashes:
                source = (project / name).resolve()
                hashes[name] = (hashlib.sha256(source.read_bytes()).hexdigest()
                                if source.is_relative_to(project) and source.is_file() else '')
            sha = file.get('sha256') or report.get('rtl_sha256')
            if not sha or hashes[name] != sha:
                continue
            for line, hits in file.get('line_hits', {}).items():
                key = line_key(instance['path'], name, line)
                lines[key] = {'instance': instance['path'], 'file': name, 'line': int(line),
                              'hit': hits > 0, 'sha256': sha}

    if report.get('functional', {}).get('status') == 'available':
        definitions = Targets(project).status()
        coverage = definitions['coverage']
        expected_revision = value.get('revision', str(value.get('epoch', '')) + ':' + str(value.get('updated', '')))
        if coverage['revision'] != expected_revision:
            raise ValueError('累计覆盖正在更新，请稍后重新读取')
        for card in definitions['targets']:
            record = coverage['targets'].get(card['id'], {})
            if card['kind'] != 'testpoint' or card['lifecycle'] != 'ready' or record.get('status') != 'available':
                continue
            observations = record['observations']
            total = sum(row['total'] for row in observations)
            if total:
                targets[card['id']] = {
                    'id': card['id'], 'name': card['name'], 'total': total,
                    'covered': sum(row['covered'] for row in observations),
                    'bindings': {binding['id']: binding['fingerprint'] for binding in card['bindings']},
                }
    return {'build_id': value.get('build_id') or report.get('build_id'), 'epoch': value.get('epoch'),
            'runs': sorted({row['id'] for row in value.get('runs', [])}),
            'lines': lines, 'targets': targets, 'error': value.get('ingestion_error')}


def total(covered, measurable):
    return {'covered': covered, 'total': measurable, 'percent': 100 * covered / measurable if measurable else None}


def metrics(snapshot):
    targets = snapshot['targets'].values()
    lines = snapshot['lines'].values()
    return {'functional': total(sum(row['covered'] for row in targets), sum(row['total'] for row in targets)),
            'line': total(sum(row['hit'] for row in lines), len(lines))}


def gaps(snapshot, offset):
    targets = [dict(id=row['id'], name=row['name'], covered=row['covered'], total=row['total'])
               for row in snapshot['targets'].values() if row['covered'] < row['total']]
    lines = [{key: row[key] for key in ('instance', 'file', 'line')}
             for row in snapshot['lines'].values() if not row['hit']]
    lines.sort(key=lambda row: (row['file'], row['line'], row['instance']))
    return {'functional': targets[offset:offset+PAGE_SIZE], 'line': lines[offset:offset+PAGE_SIZE],
            'offset': offset, 'next_offset': offset+PAGE_SIZE if max(len(targets), len(lines)) > offset+PAGE_SIZE else None,
            'functional_remaining': len(targets), 'line_remaining': len(lines)}


def delta(before, after, run_ids):
    """Attribute gains only to a comparable snapshot published by this round."""
    if not before['build_id'] or before['build_id'] != after['build_id'] or before['epoch'] != after['epoch']:
        return {'status': 'uncomparable', 'reason': '构建或覆盖累计已变化'}
    old_runs, new_runs = set(before['runs']), set(after['runs'])
    if not old_runs <= new_runs or new_runs - old_runs - run_ids:
        return {'status': 'uncomparable', 'reason': '累计中包含本轮之外的变化'}
    if not new_runs - old_runs:
        return {'status': 'uncomparable', 'reason': '本轮没有发布可用覆盖数据'}
    old_lines, new_lines = before['lines'], after['lines']
    old_targets, new_targets = before['targets'], after['targets']
    line_comparable = bool(old_lines) and old_lines.keys() == new_lines.keys() and all(
        row['sha256'] == new_lines[key]['sha256'] and (not row['hit'] or new_lines[key]['hit'])
        for key, row in old_lines.items())
    functional_comparable = bool(old_targets) and old_targets.keys() == new_targets.keys() and all(
        row['bindings'] == new_targets[key]['bindings'] and row['total'] == new_targets[key]['total']
        and row['covered'] <= new_targets[key]['covered'] for key, row in old_targets.items())
    if not (line_comparable or functional_comparable):
        return {'status': 'uncomparable', 'reason': '可计量的源码行或功能点在本轮发生变化'}
    new_hits = [{field: row[field] for field in ('instance', 'file', 'line')}
                for key, row in new_lines.items() if line_comparable and row['hit'] and not old_lines[key]['hit']]
    improved = [dict(id=key, name=row['name'], before=old_targets[key]['covered'],
                     after=row['covered'], total=row['total'])
                for key, row in new_targets.items()
                if functional_comparable and row['covered'] > old_targets[key]['covered']]
    return {'status': 'comparable', 'new_lines': new_hits, 'improved_targets': improved,
            'compared': {'line': line_comparable, 'functional': functional_comparable}}


def queue(kind, scope, proposals=()):
    return {'id': uuid.uuid4().hex, 'kind': kind, 'scope': scope,
            'proposals': [proposal.model_dump(mode='json') for proposal in proposals]}


def prepare_round(project, state, proposals):
    """Schedule only ready cards whose accepted implementation is new this round."""
    by_id = {row['case_id']: row for row in regression_scope(project)}
    reports = Cases(project).status()['reports']
    baseline = state['current']
    if not proposals or len({proposal.case_id for proposal in proposals}) != len(proposals):
        raise ValueError('本轮需提交至少一个用例且用例 ID 不可重复')
    selected = []
    for proposal in proposals:
        row = by_id.get(proposal.case_id)
        if not row or row.get('blocked') or reports[proposal.case_id]['status'] != 'bound':
            raise ValueError('请先完成用例登记并核对源码绑定：' + proposal.case_id)
        if state['case_versions'].get(proposal.case_id) == row['identity']:
            raise ValueError('只能提交本轮新增或实际修改的用例：' + proposal.case_id)
        for target in proposal.target_ids:
            gap = baseline['targets'].get(target)
            if not gap or gap['covered'] == gap['total']:
                raise ValueError('当前基线没有该功能覆盖缺口：' + target)
        for line in proposal.lines:
            gap = baseline['lines'].get(line_key(line.instance, line.file, line.line))
            if not gap or gap['hit']:
                raise ValueError(f'当前基线没有此行覆盖缺口：{line.file}:{line.line}')
        selected.append(row)
    return queue('round', selected, proposals)


def seed_receipts(project, pending, outcomes):
    """Verify the replay seeds actually reported by the random test runs."""
    runs = {row['case_id']: row.get('run_id') for row in outcomes['items']}
    receipts = []
    for proposal in pending['proposals']:
        if proposal['strategy'] != 'random':
            continue
        case_id = proposal['case_id']
        run_id = runs[case_id]
        log = project / 'output/flowcanvas/test-runs' / (run_id + '.log') if run_id else None
        text = log.read_text(errors='replace') if log and log.is_file() else ''
        logged = {int(seed) for seed in re.findall(r'\bFLOWCANVAS_SEED=([0-9]{1,20})\b', text)}
        receipts.append({'case_id': case_id, 'run_id': run_id, 'seeds': proposal['seeds'],
                         'verified': set(proposal['seeds']) <= logged})
    return receipts


def stop_condition(state):
    coverage = metrics(state['current'])
    if all(row['percent'] is None for row in coverage.values()):
        return '当前构建没有可用于补强的覆盖观测'
    options = state['options']
    limits = {metric: options[metric+'_target'] for metric in coverage if options[metric+'_target'] is not None}
    if limits and all(coverage[key]['percent'] is not None and coverage[key]['percent'] >= value
                      for key, value in limits.items()):
        return '已达到设定的覆盖目标'
    if not any([gaps(state['current'], 0)[key+'_remaining'] for key in coverage]):
        return '已有观测中没有剩余覆盖缺口'
    if len(state['rounds']) >= options['max_rounds']:
        return '已达到最大轮数'
    if state.get('deadline') and time.time() >= state['deadline']:
        return '已达到允许开启新一轮的时间预算'
    return None


class CoverageImprovementChecker(Checker):
    """Coordinate coverage baselines and evidence-checked test rounds."""

    accepted_stage_args = ('settings', 'proposals', 'stop_reason', 'offset', 'diagnoses', 'findings')

    def __init__(self, mode, **kwargs):
        super().__init__()
        self.mode = mode

    def do_check(self, settings=None, proposals=None, stop_reason=None, offset=0,
                 diagnoses=None, findings=None, is_complete=False, **kwargs):
        """Validate the baseline, queue a reviewed round, or compare completed results."""
        try:
            if type(offset) is not int or offset < 0:
                raise ValueError('offset 必须是非负整数')
            root = Path(self.workspace).parent
            request = json.loads((root / 'request.json').read_text())
            project = Path(request['project_root']).resolve()
            path = root / STATE
            if self.mode == 'baseline':
                return self.baseline(project, path, settings)
            if self.mode == 'failures':
                return self.review_failures(project, path, diagnoses, findings, is_complete)
            if self.mode != 'rounds':
                raise ValueError('未知覆盖率补强阶段')
            return self.rounds(project, path, proposals, stop_reason, offset, is_complete)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return False, {'error': str(exc)}

    def baseline(self, project, path, settings):
        state = read_state(path)
        if not state:
            options = Options.model_validate(settings if settings is not None else {})
            scope = regression_scope(project)
            snapshot = coverage_snapshot(project)
            wait_for_baseline = (not snapshot['lines'] and not snapshot['targets'] and
                                 Cases(project).read().coverage and any(not row.get('blocked') for row in scope))
            state = {'options': options.model_dump(), 'rounds': [], 'case_versions': {
                row['case_id']: row['identity'] for row in scope},
                'current': None if wait_for_baseline else snapshot,
                'pending': queue('baseline', scope) if wait_for_baseline else None}
            if options.time_budget_minutes:
                state['deadline'] = time.time() + options.time_budget_minutes * 60
            atomic(path, state)
        elif settings is not None and Options.model_validate(settings).model_dump() != state['options']:
            raise ValueError('本次运行的覆盖目标和预算已保存，调整需要启动新工作流')
        pending = state['pending']
        if pending:
            ok, runs = batch_results(project, pending['id'], pending['scope'])
            if not ok:
                return False, self.progress(pending, runs)
            state['baseline_runs'] = runs
            if any(row['outcome'] == 'failed' for row in runs['items']):
                state['stop'] = '基线运行有失败，先复核失败原因'
            state['current'] = coverage_snapshot(project)
            state['pending'] = None
        state['baseline'] = state['current']
        atomic(path, state)
        return True, {'baseline': metrics(state['baseline']), 'build_id': state['baseline']['build_id'],
                      'baseline_runs': state.get('baseline_runs'), 'coverage_error': state['baseline']['error']}

    def review_failures(self, project, path, diagnoses, findings, complete):
        state = read_state(path)
        runs = [row['results'] for row in state['rounds']]
        if state.get('baseline_runs'):
            runs.insert(0, state['baseline_runs'])
        failed = [item for batch in runs for item in batch['items'] if item['outcome'] == 'failed']
        if failed and (diagnoses is None or findings is None):
            return False, {'failed_runs': failed,
                           'next_action': '提交 diagnoses 和 findings，无法归因的失败标记为 unresolved'}
        report = analyze(project, {'items': failed}, DIAGNOSES.validate_python([] if diagnoses is None else diagnoses),
                         FINDINGS.validate_python([] if findings is None else findings))
        result = {'failed_runs': failed, 'classifications': report['classifications'],
                  **publish_findings(project, report, save=complete)}
        if complete:
            summary = path.parent / 'coverage-improvement-summary.json'
            atomic(summary, {**json.loads(summary.read_text()), 'failure_review': result})
        return True, result

    def progress(self, pending, runs):
        return {'batch_id': pending['id'], 'phase': pending['kind'], **runs}

    def rounds(self, project, path, proposals, reason, offset, complete):
        state = read_state(path)
        pending = state['pending']
        if pending:
            ok, runs = batch_results(project, pending['id'], pending['scope'])
            if not ok:
                return False, self.progress(pending, runs)
            if proposals is not None:
                raise ValueError('先用 Check 复核上一轮结果，再提交新一轮')
            after = coverage_snapshot(project)
            run_ids = {row['run_id'] for row in runs['items'] if row.get('run_id')}
            change = delta(state['current'], after, run_ids)
            seeds = seed_receipts(project, pending, runs)
            state['rounds'].append({'batch_id': pending['id'], 'results': runs, 'delta': change, 'seeds': seeds,
                                    'before': metrics(state['current']), 'after': metrics(after)})
            state['pending'] = None
            state['current'] = after
            state['case_versions'] = {row['case_id']: row['identity'] for row in regression_scope(project)}
            if change['status'] != 'comparable':
                state['stop'] = change['reason']
            elif any(row['outcome'] != 'passed' for row in runs['items']):
                state['stop'] = '本轮有失败或无效运行，待复核后再继续补强'
            elif any(not receipt['verified'] for receipt in seeds):
                state['stop'] = '随机用例缺少实际运行 seed 的日志证据'
            elif not change['new_lines'] and not change['improved_targets']:
                state['stop'] = '本轮没有新增覆盖命中'
            atomic(path, state)
        stopped = state.get('stop') or stop_condition(state)
        if proposals is not None:
            if stopped:
                raise ValueError(stopped)
            selected = prepare_round(project, state, PROPOSALS.validate_python(proposals))
            state['pending'] = selected
            atomic(path, state)
            return False, self.progress(selected, {'status': 'queued', 'items': selected['scope']})
        if reason is not None:
            if not isinstance(reason, str) or not reason.strip() or not complete:
                raise ValueError('stop_reason 只能通过 Complete 提交，并说明阻塞原因')
            stopped = stopped or reason.strip()
        result = {'baseline': metrics(state['baseline']), 'current': metrics(state['current']),
                  'rounds_completed': len(state['rounds']), 'stop_reason': stopped,
                  'gaps': gaps(state['current'], offset)}
        if state['rounds']:
            result['last_round'] = state['rounds'][-1]
        if complete and stopped:
            atomic(path.parent / 'coverage-improvement-summary.json', {**result, 'rounds': state['rounds']})
        return bool(stopped) if complete else True, result
