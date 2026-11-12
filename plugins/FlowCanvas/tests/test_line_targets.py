"""Line-based target bindings need no SystemVerilog preprocessor or parser."""
import json
from pathlib import Path
import pytest
from flowcanvas.targets import Targets, execute
from flowcanvas.target_import_workflow import TargetImportChecker


@pytest.fixture
def setup(tmp_path):
    """Create a tool caller with current revisions."""
    def call(operation, **kw):
        return execute(tmp_path,dict(operation=operation,expected_revision=Targets(tmp_path).read().revision,**kw),'ai')
    return tmp_path,call


def test_unknown_uvm_macros_bind_and_move(setup):
    root,call=setup
    source=root/'coverage.sv'
    source.write_text('`include "missing.svh"\n`UNKNOWN_MACRO(x)\ncovergroup c;\n  cp: coverpoint x;\nendgroup\n')
    identity=call('apply',count=1)['ids'][0]
    call('begin_modify',ids=[identity])
    call('update',cards=[dict(id=identity,kind='group',name='覆盖组',description='说明',bindings=[dict(file='coverage.sv',line=3,line_count=3)])])
    call('end_modify',ids=[identity])
    binding=Targets(root).read().targets[0].bindings[0]
    assert binding.kind=='LineRange'
    source.write_text('// moved\n'+source.read_text())
    result=call('inspect',binding_id=binding.id)
    assert result['reports'][identity]['status']=='bound'
    assert result['source']['line']==5
    source.write_text(source.read_text().replace('coverpoint x','coverpoint y'))
    assert call('inspect')['reports'][identity]['status']=='changed'


def test_nested_markers_and_atomic_failure(setup):
    root,call=setup
    source=root/'c.sv';source.write_text('covergroup c;\ncp: coverpoint x;\nendgroup\n')
    group,point=call('apply',count=2)['ids'];call('begin_modify',ids=[group,point])
    before=source.read_text()
    with pytest.raises(ValueError):
        call('update',cards=[dict(id=group,kind='group',name='组',description='说明',bindings=[dict(file='c.sv',line=1,line_count=3)]),
                             dict(id=point,kind='point',parent=group,bindings=[dict(file='c.sv',line=2,line_count=100)])])
    assert source.read_text()==before
    call('update',cards=[dict(id=group,kind='group',name='组',description='说明',bindings=[dict(file='c.sv',line=1,line_count=3)]),
                         dict(id=point,kind='point',parent=group,name='点',description='说明',bindings=[dict(file='c.sv',line=2,line_count=1)])])
    call('end_modify',ids=[group,point])
    assert all(r['status']=='bound' for r in call('inspect')['reports'].values())


def test_missing_and_duplicate_markers(setup):
    root,call=setup
    source=root/'c.py';source.write_text('assert True\n')
    identity=call('apply',count=1)['ids'][0];call('begin_modify',ids=[identity])
    call('update',cards=[dict(id=identity,kind='group',name='组',description='说明',bindings=[dict(file='c.py',line=1,line_count=1)])])
    original=source.read_text();source.write_text(original+original)
    assert call('inspect')['reports'][identity]['status']=='conflict'
    source.write_text('assert True\n')
    assert call('inspect')['reports'][identity]['status']=='missing'


def test_import_scope_accepts_unparseable_sv(setup,tmp_path):
    root,call=setup
    source=root/'c.sv';source.write_text('`include "absent.svh"\n`MACRO(x)\n')
    work=root/'run/workspace';(work/'summary').mkdir(parents=True)
    (work.parent/'request.json').write_text(json.dumps({'project_root':str(root)}))
    scope=work/'summary/import-scope.json';scope.write_text(json.dumps({'files':['c.sv']}))
    checker=TargetImportChecker();checker.workspace=str(work)
    assert checker.do_check()[0]
    identity=call('apply',count=1)['ids'][0];call('begin_modify',ids=[identity])
    call('update',cards=[dict(id=identity,kind='group',name='宏覆盖',description='宏定义的覆盖',bindings=[dict(file='c.sv',line=2,line_count=1)])])
    call('end_modify',ids=[identity])
    scope.write_text(json.dumps({'files':['c.sv'],'card_ids':[identity]}))
    checker=TargetImportChecker(final=True);checker.workspace=str(work)
    assert checker.do_check()[0],checker.do_check()
