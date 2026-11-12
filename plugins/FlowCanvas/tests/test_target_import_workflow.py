"""Import workflow gates and the UI's workflow attachment contract."""
import json
from pathlib import Path

from flowcanvas.targets import Targets, execute
from flowcanvas.target_import_workflow import TargetImportChecker
from flowcanvas.plugin import get_plugin
from ucagent.util.config import load_yaml_with_env_vars


def test_import_gate_rejects_unmapped_and_stale_sources(tmp_path):
    """Only finished, source-backed cards account for declared import candidates."""
    project=tmp_path/'project'; project.mkdir()
    work=tmp_path/'workspace'; (work/'summary').mkdir(parents=True)
    (tmp_path/'request.json').write_text(json.dumps({'project_root':str(project)}))
    source=project/'checks.py'; source.write_text('class Checks:\n    def verify_value(self, x):\n        assert x == 1\n')
    scope=work/'summary/import-scope.json'
    scope.write_text(json.dumps({'files':['checks.py'],'documents':[],'exclusions':[]}))
    checker=TargetImportChecker(); checker.workspace=str(work)
    assert checker.do_check()[0]
    checker=TargetImportChecker(final=True);checker.workspace=str(work)
    assert not checker.do_check()[0]
    store=Targets(project)
    def call(operation,**kwargs):
        """Use the real target tool transaction boundary."""
        return execute(project,{'operation':operation,'expected_revision':store.read().revision,**kwargs},'ai')
    ids=call('apply',count=3)['ids']
    call('begin_modify',ids=ids)
    call('update',cards=[{'id':ids[0],'kind':'group','name':'行为','description':'功能域',
          'bindings':[{'file':'checks.py','line':1,'line_count':3}]},
         {'id':ids[1],'kind':'point','parent':ids[0],'name':'结果','description':'检查输入',
          'bindings':[{'file':'checks.py','line':2,'line_count':2}]},
         {'id':ids[2],'kind':'testpoint','parent':ids[1],'name':'值为一','description':'断言结果',
          'bindings':[{'file':'checks.py','line':3,'line_count':1}]}])
    scope.write_text(json.dumps({'files':['checks.py'],'documents':[],'card_ids':ids}))
    call('end_modify',ids=ids,planned=True)
    assert checker.do_check()[0],checker.do_check()
    source.write_text(source.read_text().replace('== 1','== 2'))
    assert not checker.do_check()[0]


def test_empty_scope_and_false_exclusion_fail(tmp_path):
    """A document-only or nonexistent import cannot satisfy final acceptance."""
    project=tmp_path/'project';project.mkdir()
    work=tmp_path/'workspace';(work/'summary').mkdir(parents=True)
    (tmp_path/'request.json').write_text(json.dumps({'project_root':str(project)}))
    scope=work/'summary/import-scope.json';scope.write_text('{"files":[]}')
    checker=TargetImportChecker();checker.workspace=str(work)
    assert not checker.do_check()[0]
    (project/'empty.py').write_text('# no definitions\n')
    scope.write_text(json.dumps({'files':['empty.py']}))
    assert checker.do_check()[0]


def test_catalog_and_button_bind_import_workflow():
    """Register a native workflow and attach it when the import AI card sends."""
    root=Path(__file__).parents[1]/'src/flowcanvas'
    plugin=get_plugin()
    workflow=next(w for w in plugin.workflows if w.name=='import-targets')
    config=load_yaml_with_env_vars(workflow.config_file)
    assert len(config['stage'])==3
    for stage in config['stage']:
        check=stage['checker'][0]
        assert isinstance(TargetImportChecker(**check['args']),TargetImportChecker)
    catalog=load_yaml_with_env_vars(root/'workflow_catalog.yaml')
    assert any(w['id']=='import-targets' and w['enabled'] for w in catalog['workflows'])
    script=(root/'web/chat.js').read_text()
    assert "design?.kind==='import-goals'?'import-targets'" in script
    assert "design.kind==='import-goals')await request" in script
    panel=(root/'web/targets.js').read_text()
    assert "if(!design||planGoals||importGoals)" in panel
    assert '请启动附加的 import-targets 工作流' in panel


import unittest


class ImportHostTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the actual workflow host without a model or DUT simulator."""

    async def test_import_starts_without_compiled_package(self):
        """The import flow binds the project but never requires Picker outputs."""
        import tempfile
        from flowcanvas.chat import ChatService
        from flowcanvas.workflow_runtime import WorkflowRuntime
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            project=root/'project';project.mkdir()
            rtl=project/'Probe.v';rtl.write_text('module Probe; endmodule\n')
            service=ChatService(root/'state')
            service.projects.state['current']={'path':str(project)}
            owner=service.create();owner['status']='running'
            runtime=WorkflowRuntime(service,owner)
            try:
                result=await runtime.run('import-targets',str(rtl),'Probe')
                card=result['card']
                request=json.loads((Path(card['root'])/'request.json').read_text())
                assert request['project_root']==str(project.resolve())
                assert card['current_stage']=='inspect_verification_scope'
                assert len([s for s in card['stages'] if s.get('kind')!='startup'])==3
                assert not (project/'verification').exists()
            finally:
                await runtime.close()
