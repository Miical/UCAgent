"""Run a real managed batch and require evidence before publishing regression Bugs."""

import asyncio
import json
from pathlib import Path
import shlex
import sys

import pytest

from flowcanvas.bugs import Bugs
from flowcanvas.chat import ChatService
from flowcanvas.regression_workflow import batch_path
from flowcanvas.test_runner import TestRuns as Runs
from flowcanvas.testcases import Cases, execute
from flowcanvas.workflow_runtime import WorkflowRuntime


@pytest.mark.asyncio
async def test_regression_reports_all_results_and_registers_only_design_defects(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'Top.v').write_text("module Top(output wire out);\nassign out = 1'b0;\nendmodule\n")
    cases = Cases(project)
    service = ChatService(tmp_path / 'state')
    service.test_runs = Runs()
    service.projects.state['current'] = {'path': str(project)}
    owner = service.create()
    owner['status'] = 'running'
    runtime = WorkflowRuntime(service, owner)

    def case(operation, **fields):
        return execute(project, dict(operation=operation, expected_revision=cases.read().revision, **fields), 'author')

    ids = case('apply', count=3)['ids']
    case('begin_modify', ids=ids)
    cards = []
    for case_id, name, statement in zip(
        ids, ('test_ok', 'test_bad', 'test_setup'),
        ('assert 1 == 1', "assert 0 == 1, 'expected high output'", "raise RuntimeError('setup failed')"),
    ):
        source = project / (name + '.py')
        source.write_text(f'def {name}():\n    {statement}\n')
        cards.append(dict(id=case_id, name=name, title=name, description='Regression fixture',
                          runner={'command': shlex.quote(sys.executable) + ' -m pytest -q {case}',
                                  'description': 'Run the bound pytest function'},
                          bindings=[{'file': source.name, 'line': 1, 'line_count': 2}]))
    case('update', cards=cards)
    case('end_modify', ids=ids)

    try:
        result = await runtime.run('regression-review')
        card = result['card']
        root = Path(card['root'])
        assert 'tools' in result, (root / 'host.log').read_text()[-4000:]
        path = batch_path(project, card['id'])
        async with asyncio.timeout(20):
            while json.loads(path.read_text())['status'] == 'running':
                await asyncio.sleep(.1)
        batch = json.loads(path.read_text())
        await runtime.run(card_id=card['id'])
        assert json.loads(path.read_text()) == batch

        async def invoke(tool, **args):
            envelope = await runtime.invoke(card['id'], tool, args)
            return envelope['result']['content'][0]['text']

        async def complete(journal, **args):
            await invoke('SetCurrentStageJournal', journal=journal)
            result = await invoke('Complete', **args)
            assert 'check_pass: true' in result, result
            return result

        await complete('All registered cases were run.')
        outcomes = json.loads((root / 'regression-results.json').read_text())
        assert outcomes['counts'] == {'passed': 1, 'failed': 2}
        runs = {item['case_id']: item['run_id'] for item in outcomes['items']}
        diagnoses = [
            dict(run_id=runs[ids[1]], classification='dut_defect', reason='Output is tied low in the RTL'),
            dict(run_id=runs[ids[2]], classification='environment_issue', reason='The test setup raised an exception'),
        ]
        finding = dict(name='Output stuck low', description='Output is tied low', condition='Output should be high',
                       expected='1', actual='0', source_refs=[dict(file='Top.v', line=2)],
                       runs=[dict(run_id=runs[ids[1]], log_excerpt='fabricated')])
        assert '日志片段未出现在失败运行中' in await invoke('Check', stage_args={'diagnoses': diagnoses, 'findings': [finding]})
        assert not Bugs(project).read().targets
        finding['runs'][0]['log_excerpt'] = 'AssertionError: expected high output'
        await complete('Separated the DUT failure from test setup.', stage_args={'diagnoses': diagnoses, 'findings': [finding]})
        assert not Bugs(project).read().targets
        await complete('Published the regression finding.')
        rows = Bugs(project).read().targets
        assert len(rows) == 1 and rows[0].status == 'unverified'
        assert rows[0].case_ids == [ids[1]] and runs[ids[1]] in rows[0].evidence
        summary = json.loads((root / 'regression-summary.json').read_text())
        assert summary['created_bug_ids'] == [rows[0].id]
    finally:
        await runtime.close()
        await service.test_runs.batches.close()
        await service.close()
