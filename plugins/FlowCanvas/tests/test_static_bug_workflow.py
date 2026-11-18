"""Verify standalone static analysis publishes deduplicated project Bug records."""
import asyncio
import json
from pathlib import Path

import pytest

from flowcanvas.bugs import Bugs, execute
from flowcanvas.chat import ChatService
from flowcanvas.workflow_runtime import WorkflowRuntime


@pytest.mark.asyncio
async def test_static_bug_workflow_registers_only_after_acceptance_and_deduplicates(tmp_path):
    """Analyze the bound RTL with real MCP gates and retain the existing Bug on repeat."""
    asyncio.get_running_loop().set_debug(False)
    project = tmp_path / 'project'
    rtl = project / 'rtl'
    rtl.mkdir(parents=True)
    inputs = {
        'rtl/Top.sv': 'module Top(input a, output y); Child u(a, y); endmodule\n',
        'rtl/Child.sv': 'module Child(input a, output y);\n  assign y = 1\'b0;\nendmodule\n',
    }
    for relative, content in inputs.items():
        (project / relative).write_text(content)
    (rtl / 'Unrelated.sv').write_text('module Unrelated; endmodule\n')
    (project / '.flowcanvas').mkdir()
    (project / '.flowcanvas/project.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'fixture', 'name': 'Top',
        'rtl': {'top': 'Top', 'top_file': 'rtl/Top.sv'},
    }))
    service = ChatService(tmp_path / 'state')
    service.projects.state['current'] = {'path': str(project)}
    owner = service.create()
    owner['status'] = 'running'
    runtime = WorkflowRuntime(service, owner)
    store = Bugs(project)
    finding = {'name': 'Child output tied low', 'description': 'Output ignores the input.',
               'condition': 'Input a is high', 'impact': 'A high input cannot propagate.',
               'category': 'logic', 'source_refs': [{'file': 'rtl/Child.sv', 'line': 2}]}
    try:
        for repeated in (False, True):
            result = await runtime.run('static-bug-analysis')
            card = result['card']
            root = Path(card['root'])
            assert 'tools' in result, (root / 'host.log').read_text()[-4000:]
            assert card['current_stage'] == 'static_bug_analysis'
            assert 'EditTextFile' not in {tool['name'] for tool in result['tools']}
            assert 'report_file' not in card
            files = json.loads((root / 'request.json').read_text())['static_sources']
            assert set(files) == {'Top_RTL/' + file for file in inputs}

            initial = store.read().revision
            for snapshot in files:
                source = await runtime.invoke(card['id'], 'ReadTextFile', {'path': snapshot})
                assert 'endmodule' in str(source['result']['content'])
                findings = []
                if snapshot.endswith('Child.sv'):
                    findings = [{**finding, 'name': 'Child output never asserts'}] if repeated else [finding]
                response = await runtime.invoke(card['id'], 'Check', {
                    'stage_args': {'reviewed_file': snapshot, 'findings': findings}})
                assert 'total: 2' in response['result']['content'][0]['text'], response['result']
                assert store.read().revision == initial

            await runtime.invoke(card['id'], 'SetCurrentStageJournal', {'journal': 'Reviewed both RTL files.'})
            acceptance = await runtime.invoke(card['id'], 'Complete', {})
            assert 'check_pass: true' in acceptance['result']['content'][0]['text'], acceptance['result']
            async with asyncio.timeout(8):
                while not (root / 'complete.json').exists():
                    await asyncio.sleep(.1)
            assert not list((Path(card['workspace']) / 'summary').glob('*.md'))
            rows = store.read().targets
            assert len(rows) == 1
            if not repeated:
                bug = rows[0]
                assert bug.status == 'unverified'
                assert bug.condition == finding['condition'] and bug.impact == finding['impact']
                assert store.source(bug.source_refs[0].id)['line'] == 2
                execute(project, dict(operation='update', target_id=bug.id, expected_revision=initial+1,
                                      values={'name': 'User title', 'status': 'confirmed', 'evidence': 'Reviewed'}))
            else:
                assert rows[0].id == bug.id and rows[0].status == 'confirmed' and rows[0].name == 'User title'
                assert store.read().revision == initial
            assert all((project / file).read_text() == content for file, content in inputs.items())
            await runtime.invoke(card['id'], 'Exit', {})
    finally:
        await runtime.close()
        await service.close()


def test_static_analysis_rejects_changed_rtl_before_publication(tmp_path):
    """A finished review of an older snapshot cannot register current-source findings."""
    from flowcanvas.static_bug_workflow import StaticBugPanelChecker

    project = tmp_path / 'project'
    project.mkdir()
    source = project / 'Top.sv'
    source.write_text('module Top; endmodule\n')
    work = tmp_path / 'run/workspace'
    snapshot = work / 'Top_RTL/Top.sv'
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(source.read_bytes())
    (work.parent / 'request.json').write_text(json.dumps({
        'dut': 'Top', 'project_root': str(project), 'static_sources': ['Top_RTL/Top.sv'],
    }))
    checker = StaticBugPanelChecker().set_workspace(str(work))
    assert checker.do_check(reviewed_file='Top_RTL/Top.sv', findings=[])[0]
    source.write_text('module Top; wire changed; endmodule\n')
    accepted, result = checker.do_check(is_complete=True)
    assert not accepted and 'RTL changed' in result['error']
    assert not Bugs(project).path.exists()
