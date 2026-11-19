"""Verify the workflow's own test batch and publish evidence-backed Bug links."""

from collections import Counter
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from ucagent.checkers.base import Checker

from .bugs import Bug, Bugs
from .smoke_runs import case_identity
from .target_explanations import SourceReference, read_locations
from .targets import LOCK, atomic, new_id
from .test_runner import TestRuns
from .testcases import Cases


def batch_path(project, identity):
    return Path(project) / 'output/flowcanvas/test-runs/batches' / (identity + '.json')


def regression_scope(project):
    """Include every registered case, retaining why an unfinished card cannot run."""
    rows = []
    for case in Cases(project).read().targets:
        if case.lifecycle == 'draft':
            continue
        row = {'case_id': case.id, 'name': case.title or case.name, 'identity': case_identity(case)}
        if case.lifecycle != 'ready':
            row['blocked'] = '用例正在创建、修改或删除'
        elif not case.runner:
            row['blocked'] = '用例未配置运行命令'
        rows.append(row)
    return rows


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    run_id: str
    classification: Literal['dut_defect', 'test_issue', 'environment_issue', 'unresolved']
    reason: str = Field(min_length=1, max_length=2000)


class RunCitation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    run_id: str
    log_excerpt: str = Field(min_length=1, max_length=500)


class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    condition: str = Field(min_length=1, max_length=2000)
    expected: str = Field(min_length=1, max_length=2000)
    actual: str = Field(min_length=1, max_length=2000)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=12)
    runs: list[RunCitation] = Field(min_length=1)
    bug_id: str | None = None


DIAGNOSES = TypeAdapter(list[Diagnosis])
FINDINGS = TypeAdapter(list[Finding])


def batch_results(project, identity, scope):
    """Read terminal outcomes without reconciling runs owned by the web process."""
    expected = {row['case_id'] for row in scope if not row.get('blocked')}
    path = batch_path(project, identity)
    if expected and not path.is_file():
        return False, {'status': 'waiting_for_batch'}
    batch = json.loads(path.read_text()) if expected else {'status': 'completed', 'items': []}
    items = {row['case_id']: row for row in batch['items']}
    if set(items) != expected:
        raise ValueError('回归批次与工作流记录的用例范围不匹配')
    runs = TestRuns()
    if batch['status'] in {'running', 'stopping'}:
        progress = []
        for item in items.values():
            row = dict(item)
            if row.get('run_id'):
                row['status'] = runs.output(project, row['run_id'])['run']['status']
            progress.append(row)
        return False, {'status': batch['status'], 'batch_id': identity, 'items': progress}

    store = Cases(project)
    current = {case.id: case for case in store.read().targets}
    reports = store.status()['reports'] if expected else {}
    outcomes = []
    for case in scope:
        case_id = case['case_id']
        row = {'case_id': case_id, 'name': case['name']}
        item = items.get(case_id, {})
        run_id = item.get('run_id')
        if not run_id:
            row.update(outcome='not_run', reason=case.get('blocked') or item.get('error') or '批次未运行该用例')
        else:
            run = runs.output(project, run_id)['run']
            if run.get('batch_id') != identity or run['case_id'] != case_id:
                raise ValueError('运行记录不属于此批次或用例：' + run_id)
            row['run_id'] = run_id
            recorded = run['test_result']
            case_now = current.get(case_id)
            if run['status'] in {'starting', 'running', 'interrupted'}:
                row.update(outcome='inconclusive', reason='用例运行未正常结束')
            elif (case_now is None or reports[case_id]['status'] != 'bound'
                    or run.get('case_identity') != case['identity']
                    or case_identity(case_now) != case['identity']):
                row.update(outcome='inconclusive', reason='用例或源码绑定在回归期间发生变化')
            else:
                row.update(outcome=recorded['status'] or 'inconclusive', reason=recorded['reason'])
        outcomes.append(row)
    return True, {'batch_id': identity, 'status': batch['status'], 'items': outcomes,
                  'counts': dict(Counter(row['outcome'] for row in outcomes))}


def analyze(project, outcomes, diagnoses, findings):
    """Require classifications for each failed run and cite actual log excerpts."""
    failed = {row['run_id']: row['case_id'] for row in outcomes['items'] if row['outcome'] == 'failed'}
    by_run = {row.run_id: row for row in diagnoses}
    if len(by_run) != len(diagnoses) or set(by_run) != set(failed):
        raise ValueError('每个失败运行都需要一条归因，其他结果请保留为待查')
    defect_runs = {row.run_id for row in diagnoses if row.classification == 'dut_defect'}
    cited = {citation.run_id for finding in findings for citation in finding.runs}
    if cited != defect_runs:
        raise ValueError('只可登记已归因为 DUT 缺陷的运行，且每个此类运行必须有关联发现')
    store = Bugs(project)
    proposals = []
    for finding in findings:
        for ref in finding.source_refs:
            if Path(ref.file).is_absolute() or Path(ref.file).suffix.lower() not in {'.v', '.sv', '.vh', '.svh', '.vhd', '.vhdl'}:
                raise ValueError('缺陷源码依据须为项目相对 RTL 路径')
        for citation in finding.runs:
            log = store.root / 'output/flowcanvas/test-runs' / (citation.run_id + '.log')
            text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', log.read_text(errors='replace'))
            if citation.log_excerpt not in text:
                raise ValueError('日志片段未出现在失败运行中：' + citation.run_id)
        fields = {'name': finding.name, 'description': finding.description, 'condition': finding.condition,
                  'expected': finding.expected, 'actual': finding.actual,
                  'source_refs': [ref.model_dump() for ref in finding.source_refs]}
        # Anchor evidence at analysis so publication can detect a concurrent RTL edit.
        candidate = store.patch_bug(Bug(id=new_id('bug'), name=finding.name, origin='ai'), fields)
        proposals.append({'candidate': candidate.model_dump(mode='json'), 'bug_id': finding.bug_id,
                          'case_ids': sorted({failed[citation.run_id] for citation in finding.runs}),
                          'evidence': [{'run_id': citation.run_id, 'log_excerpt': citation.log_excerpt}
                                       for citation in finding.runs]})
    return {'classifications': dict(Counter(row.classification for row in diagnoses)),
            'diagnoses': [row.model_dump() for row in diagnoses], 'findings': proposals}


def publish_findings(project, analysis, *, save):
    """Reuse Bug IDs and preserve human reviews while publishing this batch's evidence."""
    with LOCK:
        store = Bugs(project)
        definition = store.read()
        created, linked = [], set()
        changed = False
        for finding in analysis['findings']:
            candidate = Bug.model_validate(finding['candidate'])
            for ref in candidate.source_refs:
                # Require the same cited source, even if unrelated lines shifted.
                read_locations(store.file(ref.file)).resolve(ref.anchor)
            bug_id = finding['bug_id']
            if bug_id:
                bug = next((row for row in definition.targets if row.id == bug_id), None)
                if not bug:
                    raise ValueError('要同步的已有 Bug 不存在：' + bug_id)
            else:
                primary = candidate.source_refs[0]
                bug = next((row for row in definition.targets if not row.example
                            and row.name.casefold() == candidate.name.casefold()
                            and any(ref.file == primary.file and ref.anchor.fingerprint == primary.anchor.fingerprint
                                    for ref in row.source_refs)), None)
            if bug is None:
                bug = candidate
                definition.targets.append(bug)
                created.append(bug.id)
                changed = True
            elif bug.lifecycle != 'ready' or bug.example or bug.status == 'as_designed':
                raise ValueError('不可将此 Bug 作为回归缺陷复用：' + bug.id)
            if bug.id not in created:
                linked.add(bug.id)
            case_ids = list(dict.fromkeys([*bug.case_ids, *finding['case_ids']]))
            evidence = bug.evidence
            for item in finding['evidence']:
                line = '运行 ' + item['run_id'] + '：' + item['log_excerpt']
                if line not in evidence:
                    evidence = '\n\n'.join(part for part in (evidence, line) if part)
            if bug.case_ids != case_ids or evidence != bug.evidence:
                bug.case_ids, bug.evidence = case_ids, evidence
                changed = True
        if save and changed:
            store.save(definition, definition.revision)
        return {'created_bug_ids': created, 'linked_bug_ids': sorted(linked)}


class RegressionChecker(Checker):
    """Gate batch completion, failure analysis, and Bug publication."""

    accepted_stage_args = ('diagnoses', 'findings')

    def __init__(self, mode, **kwargs):
        super().__init__()
        self.mode = mode

    def get_template_data(self):
        path = Path(self.workspace).parent / 'regression-results.json'
        return {'REGRESSION_RESULTS': path.read_text() if path.is_file() else '{}'}

    def do_check(self, diagnoses=None, findings=None, is_complete=False, **kwargs):
        """Validate real batch results and source-backed diagnosis for this stage."""
        try:
            root = Path(self.workspace).parent
            request = json.loads((root / 'request.json').read_text())
            project = Path(request['project_root'])
            result_path = root / 'regression-results.json'
            diagnosis_path = root / 'regression-analysis.json'
            if self.mode == 'runs':
                ok, result = batch_results(project, root.name, request['regression_cases'])
                if ok and is_complete:
                    atomic(result_path, result)
                return ok, result
            outcomes = json.loads(result_path.read_text())
            if self.mode == 'analysis':
                if not isinstance(diagnoses, list) or not isinstance(findings, list):
                    raise ValueError('请提交 diagnoses 和 findings 数组，允许空数组')
                result = analyze(project, outcomes, DIAGNOSES.validate_python(diagnoses),
                                 FINDINGS.validate_python(findings))
                publish_findings(project, result, save=False)
                if is_complete:
                    atomic(diagnosis_path, result)
                return True, {'classifications': result['classifications'], 'findings': len(result['findings'])}
            if self.mode != 'publish':
                raise ValueError('未知回归验收阶段')
            ok, current = batch_results(project, root.name, request['regression_cases'])
            if not ok or current != outcomes:
                raise ValueError('回归运行或用例源码有变化，请重新运行回归后再登记')
            result = json.loads(diagnosis_path.read_text())
            summary = {'batch_id': outcomes['batch_id'], 'counts': outcomes['counts'],
                       'classifications': result['classifications'],
                       **publish_findings(project, result, save=is_complete)}
            if is_complete:
                atomic(root / 'regression-summary.json', summary)
            return True, summary
        except (OSError, KeyError, ValueError) as exc:
            return False, {'error': str(exc)}
