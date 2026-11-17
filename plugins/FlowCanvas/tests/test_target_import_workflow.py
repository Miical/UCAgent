"""Import workflow gates and the UI's workflow attachment contract."""
import json
import pytest
from pathlib import Path

from flowcanvas.targets import Targets, execute
from flowcanvas.target_import_workflow import TargetImportChecker
from flowcanvas.plugin import get_plugin
from ucagent.util.config import load_yaml_with_env_vars


@pytest.mark.parametrize('point,bin_decl,accepted',[
    ('addr_cp: coverpoint addr {','bins one = {1};',True),
    ('addr_cross: cross addr_cp, op_cp {','bins one = binsof(addr_cp);',True),
    ('addr_cp: coverpoint addr {','wildcard bins one = {1};',True),
    ('assert property (addr == 1);','bins one = {1};',False),
    ('if (addr != 1) `uvm_error("CHECK", "coverpoint addr")','bins one = {1};',False),
    ('// addr_cp: coverpoint addr\nassert property (addr);','bins one = {1};',False),
    ('addr_cp: coverpoint addr {','ignore_bins one = {1};',False),
    ('addr_cp: coverpoint addr {','illegal_bins one = {1};',False),
])
def test_import_gate_rejects_unmapped_and_stale_sources(tmp_path,point,bin_decl,accepted):
    """Only finished, source-backed cards account for declared import candidates."""
    project=tmp_path/'project'; project.mkdir()
    work=tmp_path/'workspace'; (work/'summary').mkdir(parents=True)
    (tmp_path/'request.json').write_text(json.dumps({'project_root':str(project)}))
    source=project/'coverage.sv'; source.write_text('covergroup checks;\n'+point+'\n'+bin_decl+'\n}\nendgroup\n')
    scope=work/'summary/import-scope.json'
    scope.write_text(json.dumps({'files':['coverage.sv'],'documents':[],'exclusions':[]}))
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
          'bindings':[{'file':'coverage.sv','line':1,'line_count':len(source.read_text().splitlines())}]},
         {'id':ids[1],'kind':'point','parent':ids[0],'name':'结果','description':'检查输入',
          'bindings':[{'file':'coverage.sv','line':2,'line_count':1}]},
         {'id':ids[2],'kind':'testpoint','parent':ids[1],'name':'值为一','description':'断言结果',
          'bindings':[{'file':'coverage.sv','line':len(point.splitlines())+2,'line_count':1}]}])
    scope.write_text(json.dumps({'files':['coverage.sv'],'documents':[],'card_ids':ids}))
    call('end_modify',ids=ids,planned=True)
    assert checker.do_check()[0] is accepted,checker.do_check()
    if not accepted:
        assert 'Import only' in checker.do_check()[1]['error']
        return
    source.write_text(source.read_text().replace('covergroup checks','covergroup changed'))
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
    assert "design.kind==='import-goals'" in next(line for line in script.splitlines() if "/attachment" in line and "await request" in line and "import-testcases" in line).split("await request")[0]
    panel=(root/'web/targets.js').read_text()
    assert "if(!design||planGoals||importGoals||importCases)" in panel
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


def test_synchronization_preserves_existing_content_and_adds_missing(tmp_path):
    """Repeat synchronization without writes, add a point, and require reasons for existing repairs."""
    project=tmp_path/'project';project.mkdir()
    source=project/'coverage.sv';source.write_text('covergroup cg;\ncp: coverpoint value;\nendgroup\n')
    work=tmp_path/'workspace';(work/'summary').mkdir(parents=True)
    store=Targets(project)
    def call(operation,**kwargs):
        """Apply a real target transaction with the current revision."""
        return execute(project,{'operation':operation,'expected_revision':store.read().revision,**kwargs},'ai')
    group=call('apply',count=1)['ids'][0]
    call('begin_modify',ids=[group])
    call('update',cards=[dict(id=group,kind='group',name='已有名称',description='保留说明',bindings=[dict(file='coverage.sv',line=1,line_count=3)])])
    call('end_modify',ids=[group])
    snapshot=[row.model_dump() for row in store.read().targets]
    (tmp_path/'request.json').write_text(json.dumps(dict(project_root=str(project),target_baseline=[group],target_snapshot=snapshot)))
    scope=work/'summary/import-scope.json'
    scope.write_text(json.dumps(dict(files=['coverage.sv'],card_ids=[group],repairs=[])))
    checker=TargetImportChecker(final=True);checker.workspace=str(work)
    before=store.path.read_bytes()
    assert checker.do_check()[0],checker.do_check()
    assert checker.do_check()[0]
    assert store.path.read_bytes()==before
    point=call('apply',count=1)['ids'][0]
    line=next(index+1 for index,text in enumerate(source.read_text().splitlines()) if text.startswith('cp:'))
    call('begin_modify',ids=[point])
    call('update',cards=[dict(id=point,kind='point',parent=group,name='新增功能点',description='已有覆盖定义',bindings=[dict(file='coverage.sv',line=line,line_count=1)])])
    call('end_modify',ids=[point])
    scope.write_text(json.dumps(dict(files=['coverage.sv'],card_ids=[group,point],repairs=[])))
    assert checker.do_check()[0],checker.do_check()
    call('begin_modify',ids=[group])
    call('update',cards=[dict(id=group,name='被重写的名称')])
    call('end_modify',ids=[group])
    assert not checker.do_check()[0]
    assert 'Preserve existing' in checker.do_check()[1]['error']
    scope.write_text(json.dumps(dict(files=['coverage.sv'],card_ids=[group,point],repairs=[dict(id=group,reason='Correct a verified naming error in the existing metadata')])) )
    assert checker.do_check()[0],checker.do_check()
    call('begin_modify',ids=[group],action='delete',cascade=True)
    call('end_modify',ids=[group,point])
    scope.write_text(json.dumps(dict(files=['coverage.sv'],card_ids=[],repairs=[])))
    assert not checker.do_check()[0]
    assert 'preserve existing targets' in checker.do_check()[1]['error']


def test_synchronization_accepts_scope_without_coverage(tmp_path):
    """An empty eligible scope is a successful no-op rather than a demand for invented targets."""
    project=tmp_path/'project';project.mkdir();(project/'checks.sv').write_text('assert property (valid);\n')
    work=tmp_path/'workspace';(work/'summary').mkdir(parents=True)
    (tmp_path/'request.json').write_text(json.dumps(dict(project_root=str(project),target_baseline=[],target_snapshot=[])))
    (work/'summary/import-scope.json').write_text(json.dumps(dict(files=['checks.sv'],card_ids=[],repairs=[],exclusions=[dict(file='checks.sv',reason='Assertions are outside coverage target synchronization')])) )
    checker=TargetImportChecker(final=True);checker.workspace=str(work)
    assert checker.do_check()[0],checker.do_check()
