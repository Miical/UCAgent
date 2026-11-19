"""Exercise the Bug-to-test-case workflow on a project without an RTL selection."""

from pathlib import Path

import pytest

from flowcanvas.bugs import BugReview, Bugs, execute as bug_operation
from flowcanvas.chat import ChatService
from flowcanvas.testcases import Cases, execute as case_operation
from flowcanvas.workflow_runtime import WorkflowRuntime


@pytest.mark.asyncio
async def test_workflow_rejects_unreviewed_expectations_and_checks_applied_plan(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    source = project / 'test_counter.py'
    source.write_text('def test_rollover(dut):\n    assert dut.count == 9\n')
    cases = Cases(project)
    bugs = Bugs(project)

    def case(op, **fields):
        return case_operation(project, dict(operation=op, expected_revision=cases.read().revision, **fields), 'owner')

    def bug(op, **fields):
        return bug_operation(project, dict(operation=op, expected_revision=bugs.read().revision, **fields), 'owner')

    case_id = case('apply', count=1)['ids'][0]
    case('begin_modify', ids=[case_id])
    case('update', cards=[dict(id=case_id, name='test_rollover', title='Counter rollover',
                              description='Check rollover', runner={'command': 'pytest {case}', 'description': 'Run case'},
                              bindings=[{'file': source.name, 'line': 1, 'line_count': 2}])])
    case('end_modify', ids=[case_id])
    bug_id = bug('create', name='Counter skips zero', values={'description': 'Incorrect rollover'})['id']
    service = ChatService(tmp_path / 'state')
    service.projects.state['current'] = {'path': str(project)}
    owner = service.create()
    owner['status'] = 'running'
    runtime = WorkflowRuntime(service, owner)
    try:
        result = await runtime.run('sync-testcase-links')
        card = result['card']
        run = Path(card['root'])
        assert 'tools' in result, (run / 'host.log').read_text()[-4000:]
        plan = {
            'reviews': [dict(bug_id=bug_id, case_ids=[case_id], reason='The case exercises rollover')],
            'expectations': [dict(bug_id=bug_id, case_id=case_id, file=source.name,
                                  expected='Rollover returns zero', basis='Counter specification section 2')],
        }

        async def invoke(tool, **args):
            result = await runtime.invoke(card['id'], tool, args)
            return result['result']['content'][0]['text']

        assert 'Only a reviewed Bug' in await invoke('Check', stage_args=plan)
        assert not (run / 'testcase-link-plan.json').exists()
        def review(expected):
            bugs.review(bug_id, BugReview(expected_revision=bugs.read().revision, status='confirmed',
                                          review_expected=expected))

        review('Rollover returns zero')
        await invoke('SetCurrentStageJournal', journal='Reviewed the Bug verdict and rollover specification.')
        assert 'check_pass: true' in await invoke('Complete', stage_args=plan)
        assert 'Finish the planned Bug associations' in await invoke('Check')
        review('Rollover returns one')
        assert 'Bug review or scenario changed' in await invoke('Check')
        review('Rollover returns zero')

        bug('begin_modify', ids=[bug_id])
        bug('update', cards=[{'id': bug_id, 'case_ids': [case_id]}])
        bug('end_modify', ids=[bug_id])
        assert 'Finish and verify the test case bindings' in await invoke('Check')

        case('begin_modify', ids=[case_id])
        source.write_text(source.read_text().replace('== 9', '== 0'))
        binding = case('inspect')['reports'][case_id]['bindings'][0]
        case('update', cards=[{
            'id': case_id, 'timeline': [dict(id='rollover', title='Rollover check', description='Observe the count',
                                            expected='Rollover returns zero')],
            'bindings': [dict(id=binding['id'], file=source.name, line_count=2,
                              role='definition', fingerprint=binding['fingerprint'])],
        }])
        case('end_modify', ids=[case_id])
        await invoke('SetCurrentStageJournal', journal='Synchronized and verified the Bug link and updated assertion.')
        acceptance = await invoke('Complete')
        assert 'check_pass: true' in acceptance, acceptance
        assert 'links_added: 1' in acceptance and 'expectations_checked: 1' in acceptance
        assert not (project / 'output/flowcanvas/test-runs').exists()
    finally:
        await runtime.close()
        await service.close()
