"""Managed startup smoke behavior and import receipt acceptance."""
import asyncio
import json
from pathlib import Path
import shlex
import sys

import pytest
from flowcanvas.testcases import Cases, execute
from flowcanvas.test_runner import TestRuns as Runs
from flowcanvas.case_import_workflow import CaseImportChecker
from flowcanvas.plugin import get_plugin
from ucagent.util.config import load_yaml_with_env_vars


def make_case(root,body):
    """Build an isolated executable fixture through the public card lifecycle."""
    source=root/'test_start.py';source.write_text(body)
    def call(op,**kw):
        """Apply an owned current-revision request."""
        return execute(root,dict(operation=op,expected_revision=Cases(root).read().revision,**kw),'fixture')
    identity=call('apply',count=1)['ids'][0];call('begin_modify',ids=[identity])
    call('update',cards=[dict(id=identity,name='test_start',title='启动测试',description='观察启动',
         runner=dict(command=shlex.quote(sys.executable)+' -u test_start.py {case}',description='Fixture'),
         bindings=[dict(file=source.name,line=1,line_count=len(body.splitlines()))])])
    call('end_modify',ids=[identity])
    return identity,call


@pytest.mark.asyncio
@pytest.mark.parametrize('body,pattern,expected',[
    ('import time\nprint("READY test_start",flush=True)\ntime.sleep(30)\n','READY test_start','passed'),
    ('print("READY test_start")\n','READY test_start','passed'),
    ('print("ERROR: failed to load")\n','READY test_start','failed'),
    ('import time\ntime.sleep(30)\n','READY test_start','failed'),
    ('print("compile done")\n','READY test_start','failed'),
    ('print("READY test_start")\nraise RuntimeError("bad")\n','READY test_start','failed'),
])
async def test_smoke_outcomes(tmp_path,body,pattern,expected):
    """Readiness, failure and timeout cannot be confused with full test results."""
    identity,_=make_case(tmp_path,body)
    runner=Runs()
    try:
        run=await runner.start(tmp_path,identity,'startup_smoke',pattern,2)
        await asyncio.wait_for(asyncio.gather(*runner.tasks.values()),8)
        result=runner.output(tmp_path,run['id'])['run']
        assert result['smoke_status']==expected,result
        assert result.get('coverage_status') is None
        if 'time.sleep(30)' in body and expected=='passed':
            assert result['status']=='interrupted'
            assert result['stop_reason']=='startup_confirmed'
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_import_gate_checks_current_receipt(tmp_path):
    """A real managed run is required and changing the card invalidates it."""
    identity,call=make_case(tmp_path,'print("READY test_start")\n')
    work=tmp_path/'task/workspace';(work/'summary').mkdir(parents=True)
    (work.parent/'request.json').write_text(json.dumps({'project_root':str(tmp_path),'testcase_baseline':[]}))
    plan={'files':['test_start.py'],'card_ids':[identity],'smoke_runs':{}}
    scope=work/'summary/case-import.json';scope.write_text(json.dumps(plan))
    checker=CaseImportChecker(mode='smoke');checker.workspace=str(work)
    assert not checker.do_check()[0]
    runner=Runs()
    try:
        run=await runner.start(tmp_path,identity,'startup_smoke','READY test_start',2)
        await asyncio.gather(*runner.tasks.values())
        plan['smoke_runs'][identity]=run['id'];scope.write_text(json.dumps(plan))
        assert checker.do_check()[0],checker.do_check()
        call('begin_modify',ids=[identity])
        call('update',cards=[dict(id=identity,runner=dict(command='echo {case}',description='Changed'))])
        call('end_modify',ids=[identity])
        assert not checker.do_check()[0]
    finally:
        await runner.close()


def test_workflow_and_ui_binding():
    """The import AI action attaches the four-stage workflow."""
    workflow=next(w for w in get_plugin().workflows if w.name=='import-testcases')
    data=load_yaml_with_env_vars(workflow.config_file)
    assert len(data['stage'])==4
    root=Path(__file__).parents[1]/'src/flowcanvas'
    assert "design?.kind==='import-testcases'?'import-testcases'" in (root/'web/chat.js').read_text()
    assert '请启动附加的 import-testcases 工作流' in (root/'web/targets.js').read_text()
    assert 'mode=startup_smoke' in data['stage'][2]['task'][1]


@pytest.mark.asyncio
async def test_smoke_skips_shared_coverage(tmp_path):
    """A deliberately failing collector must never execute for startup checks."""
    from flowcanvas.testcases import configure
    identity,_=make_case(tmp_path,'print("READY test_start")\n')
    configure(tmp_path,{'coverage':dict(tool='fixture',description='Must not execute',
        artifact_path='{run_dir}/coverage.json',extract_command='false',
        merge_command='false {inputs_manifest} {merge_dir}',report_path='{merge_dir}/coverage.json',
        build_id_command='false')},Cases(tmp_path).read().revision)
    runner=Runs()
    try:
        run=await runner.start(tmp_path,identity,'startup_smoke','READY test_start',2)
        await asyncio.gather(*runner.tasks.values())
        assert runner.output(tmp_path,run['id'])['run']['smoke_status']=='passed'
        assert 'build_id' not in run
    finally:
        await runner.close()


import unittest


class CaseImportHostTests(unittest.IsolatedAsyncioTestCase):
    """Exercise workflow creation without invoking any real project test."""

    async def test_import_host_binds_project(self):
        """Start the actual four-stage MCP host without needing a compiled DUT."""
        import tempfile
        from flowcanvas.chat import ChatService
        from flowcanvas.workflow_runtime import WorkflowRuntime
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);project=root/'project';project.mkdir()
            rtl=project/'Probe.v';rtl.write_text('module Probe; endmodule\n')
            service=ChatService(root/'state');service.projects.state['current']={'path':str(project)}
            owner=service.create();owner['status']='running'
            runtime=WorkflowRuntime(service,owner)
            try:
                result=await runtime.run('import-testcases',str(rtl),'Probe')
                card=result['card']
                request=json.loads((Path(card['root'])/'request.json').read_text())
                assert request['project_root']==str(project.resolve())
                assert request['testcase_baseline']==[]
                assert card['current_stage']=='inspect_testcase_scope'
                assert len([s for s in card['stages'] if s.get('kind')!='startup'])==4
            finally:
                await runtime.close()
