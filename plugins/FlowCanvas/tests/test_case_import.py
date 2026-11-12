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



def test_workflow_and_ui_binding():
    """The import AI action attaches the nested import workflow."""
    workflow=next(w for w in get_plugin().workflows if w.name=='import-testcases')
    data=load_yaml_with_env_vars(workflow.config_file)
    assert len(data['stage'])==4
    parent=data['stage'][1]
    assert parent['name']=='import_testcases'
    assert [s['name'] for s in parent['stage']]==['import_testcase_cards','configure_testcase_runners','configure_shared_coverage']
    assert [s['checker'][0]['args']['mode'] for s in parent['stage']]==['cards','runners','coverage']
    root=Path(__file__).parents[1]/'src/flowcanvas'
    assert "design?.kind==='import-testcases'?'import-testcases'" in (root/'web/chat.js').read_text()
    assert '请启动附加的 import-testcases 工作流' in (root/'web/targets.js').read_text()
    assert '逐个进行启动冒烟检查' not in (root/'web/targets.js').read_text()
    assert '不逐个运行其他用例' in (root/'web/targets.js').read_text()
    assert 'Do not start tests' in data['stage'][3]['task'][0]
    assert data['stage'][2]['name']=='validate_coverage_smoke'
    assert any('mode=coverage_smoke' in task for task in data['stage'][2]['task'])


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
        """Start the actual nested import MCP host without needing a compiled DUT."""
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
                stages=[s for s in card['stages'] if s.get('kind')!='startup']
                assert len(stages)==7
                children=[s for s in stages if s.get('parent')=='import_testcases']
                assert len(children)==3
                assert all(s['depth']==1 for s in children)
            finally:
                await runtime.close()


def test_card_stage_does_not_require_runner_but_runner_stage_does(tmp_path):
    """Bind a new card first without fabricating a command or weakening finalization."""
    identity,call=make_case(tmp_path,'def test_start():\n    pass\n')
    call('begin_modify',ids=[identity])
    call('update',cards=[dict(id=identity,runner=None)])
    work=tmp_path/'task/workspace';(work/'summary').mkdir(parents=True)
    (work.parent/'request.json').write_text(json.dumps({'project_root':str(tmp_path),'testcase_baseline':[]}))
    (work/'summary/case-import.json').write_text(json.dumps({'files':['test_start.py'],'card_ids':[identity]}))
    cards=CaseImportChecker(mode='cards');cards.workspace=str(work)
    runners=CaseImportChecker(mode='runners');runners.workspace=str(work)
    assert cards.do_check()[0],cards.do_check()
    assert not runners.do_check()[0]
    with pytest.raises(ValueError,match='runner'):
        call('end_modify',ids=[identity])
    call('update',cards=[dict(id=identity,runner=dict(command='python -m pytest {case}',description='Real entry'))])
    call('end_modify',ids=[identity])
    assert runners.do_check()[0],runners.do_check()
