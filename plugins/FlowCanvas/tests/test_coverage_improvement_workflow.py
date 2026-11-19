"""Exercise real baseline and improvement batches without a simulator."""

import asyncio
import json
from pathlib import Path
import shlex
import sys

import pytest

from flowcanvas.chat import ChatService
from flowcanvas.coverage_improvement import queued_batch
from flowcanvas.regression_workflow import batch_path
from flowcanvas.test_runner import TestRuns as Runs
from flowcanvas.testcases import Cases, configure, execute
from flowcanvas.workflow_runtime import WorkflowRuntime


@pytest.mark.asyncio
async def test_coverage_workflow_runs_baseline_and_measures_a_seeded_round(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'Top.v').write_text('module Top;\nwire boundary;\nendmodule\n')
    (project / 'runner.py').write_text("""import sys
from pathlib import Path
db, _, case = sys.argv[1:]
path = Path(db)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(str(max(int(path.read_text()) if path.exists() else 1, 2 if 'random' in case else 1)))
if 'random' in case:
    print('FLOWCANVAS_SEED=42')
print('TEST PASSED')
""")
    (project / 'report.py').write_text("""import hashlib, json, sys
from pathlib import Path
db, destination = map(Path, sys.argv[1:])
hits = int(db.read_text())
sha = hashlib.sha256(Path('Top.v').read_bytes()).hexdigest()
files = [dict(path='Top.v', sha256=sha, line_hits={'1': 1, '2': int(hits == 2)})]
Path(destination).write_text(json.dumps(dict(schema_version=1, build_id='fixture',
    instances=[dict(path='Top', covered=hits, total=2, files=files)])))
""")
    store = Cases(project)
    python = shlex.quote(sys.executable)

    def case(operation, **fields):
        return execute(project, dict(operation=operation, expected_revision=store.read().revision, **fields), 'author')

    def register(name):
        path = project / (name + '.py')
        path.write_text('def ' + name + '():\n    assert True\n')
        identity = case('apply', count=1)['ids'][0]
        case('begin_modify', ids=[identity])
        case('update', cards=[dict(id=identity, name=name, title=name, description='Coverage fixture',
                                  runner={'command': python + ' runner.py {coverage_db} {coverage_name} {case}',
                                          'description': 'Run the fixture'},
                                  bindings=[dict(file=path.name, line=1, line_count=2)])])
        case('end_modify', ids=[identity])
        return identity

    register('test_baseline')
    configure(project, {'coverage': dict(mode='shared', tool='fixture', description='Fixture coverage writer',
                                        extract_command=python + ' report.py {coverage_db} {run_dir}/coverage.json',
                                        build_id_command='echo fixture')}, store.read().revision)
    service = ChatService(tmp_path / 'state')
    service.test_runs = Runs()
    service.projects.state['current'] = {'path': str(project)}
    owner = service.create()
    owner['status'] = 'running'
    runtime = WorkflowRuntime(service, owner)
    try:
        started = await runtime.run('coverage-improvement')
        card = started['card']
        root = Path(card['root'])
        assert 'tools' in started, (root / 'host.log').read_text()[-4000:]

        async def invoke(tool, **args):
            result = await runtime.invoke(card['id'], tool, args)
            return result['result']['content'][0]['text']

        async def finish_pending_batch():
            queued = queued_batch(root)
            path = batch_path(project, queued['id'])
            async with asyncio.timeout(20):
                while json.loads(path.read_text())['status'] == 'running':
                    await asyncio.sleep(.1)

        await invoke('Check', stage_args={'settings': {'line_target': 100, 'max_rounds': 2}})
        await finish_pending_batch()
        await invoke('SetCurrentStageJournal', journal='Established a 50% line coverage baseline.')
        baseline = await invoke('Complete')
        assert 'check_pass: true' in baseline, baseline
        candidate = register('test_random')
        await invoke('Check', stage_args={'proposals': [dict(case_id=candidate, strategy='random', seeds=[42],
                                                            lines=[dict(instance='Top', file='Top.v', line=2)],
                                                            reason='Exercise the unvisited boundary')]})
        await finish_pending_batch()
        await invoke('SetCurrentStageJournal', journal='Measured the seeded round.')
        result = await invoke('Complete')
        assert 'check_pass: true' in result, result
        await invoke('SetCurrentStageJournal', journal='The seeded round produced no failures.')
        result = await invoke('Complete')
        assert 'check_pass: true' in result, result
        summary = json.loads((root / 'coverage-improvement-summary.json').read_text())
        assert summary['baseline']['line']['percent'] == 50
        assert summary['current']['line']['percent'] == 100
        assert summary['current']['functional']['percent'] is None
        assert summary['rounds_completed'] == 1
        assert summary['rounds'][0]['delta']['new_lines'] == [dict(instance='Top', file='Top.v', line=2)]
        assert summary['rounds'][0]['seeds'][0]['verified'] is True
        assert summary['failure_review']['failed_runs'] == []
    finally:
        await runtime.close()
        await service.test_runs.close()
        await service.close()


def test_coverage_gain_requires_same_build_and_round_provenance():
    from flowcanvas.coverage_improvement import delta

    before = {'build_id': 'same', 'epoch': 'epoch', 'runs': ['baseline'], 'lines': {},
              'targets': {'target': {'name': 'Boundary', 'covered': 0, 'total': 2, 'bindings': {'binding': 'hash'}}}}
    target = dict(before['targets']['target'], covered=1)
    after = {**before, 'runs': ['baseline', 'round'], 'targets': {'target': target}}
    gain = delta(before, after, {'round'})
    assert gain['compared'] == {'line': False, 'functional': True}
    assert gain['improved_targets'] == [{'id': 'target', 'name': 'Boundary', 'before': 0, 'after': 1, 'total': 2}]
    for incompatible in ({**after, 'build_id': 'recompiled'}, {**after, 'runs': ['baseline', 'round', 'external']},
                         {**after, 'targets': {'target': dict(target, bindings={'binding': 'edited'})}}):
        assert delta(before, incompatible, {'round'})['status'] == 'uncomparable'
