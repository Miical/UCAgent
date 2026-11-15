"""Check the standard module-generation contract and reject ungrounded or stale writes."""
import copy
from types import SimpleNamespace
import pytest
from flowcanvas.module_overviews import execute


def test_source_backed_module_overview(tmp_path):
    """Save a semantic graph, reject bad citations and preserve edits on revision conflicts."""
    source=tmp_path/'unit.sv';source.write_text('module unit(input clk);\nwire ready = clk;\nendmodule\n')
    project=dict(id='p',path=str(tmp_path))
    service=SimpleNamespace(projects=SimpleNamespace(state={'recent':[project]}),rtl_view=lambda _:dict(top='unit',nodes=[dict(id='unit',module='unit',ports=[dict(name='clk',dir='input')],source=dict(file=str(source),start_line=1,end_line=3))]))
    session=dict(case_binding=dict(project_id='p',case_id='module_design'),permission_mode='workspace')
    inspected=execute(service,session,'inspect','unit',None,'','')
    assert '模块概览编写规范' in inspected['guide']
    assert inspected['guide_path'].endswith('module-overview-guide.md')
    explanation=dict(text='A source-backed explanation',line=2,assignments=[dict(expression='wire ready = clk;',meaning='Copy the input',line=2)])
    overview=dict(purpose=explanation,structure=dict(nodes=[dict(id='logic',title='Control',detail='ready',text='Connect input to the ready signal',line=2)],edges=[]),mechanisms=[explanation],rules=[explanation],ports={'clk':'Input'})
    tokens=(inspected['expected_revision'],inspected['source_revision'])
    bad=copy.deepcopy(overview);bad['structure']['edges']=[{'from':'logic','to':'missing','label':'invalid'}]
    with pytest.raises(ValueError,match='endpoint'):execute(service,session,'save','unit',bad,*tokens)
    bad=copy.deepcopy(overview);bad['purpose']['assignments'][0]['expression']='wire fabricated = 1;'
    with pytest.raises(ValueError,match='excerpt'):execute(service,session,'save','unit',bad,*tokens)
    bad=copy.deepcopy(overview);bad['purpose']['file']='../outside.sv'
    with pytest.raises(ValueError,match='escapes'):execute(service,session,'save','unit',bad,*tokens)
    session['permission_mode']='read'
    with pytest.raises(ValueError,match='Workspace'):execute(service,session,'save','unit',overview,*tokens)
    session['permission_mode']='workspace'
    saved=execute(service,session,'save','unit',overview,*tokens)
    assert saved['saved'] and 'x' not in saved['overview']['structure']['nodes'][0]
    assert execute(service,session,'inspect','unit',None,'','')['overview']==saved['overview']
    with pytest.raises(ValueError,match='changed'):execute(service,session,'save','unit',overview,*tokens)
    latest=execute(service,session,'inspect','unit',None,'','');source.write_text(source.read_text()+'// changed\n')
    with pytest.raises(ValueError,match='changed'):execute(service,session,'save','unit',overview,latest['expected_revision'],latest['source_revision'])

    model=service.rtl_view('p')
    model['nodes'].append(dict(id='unit.child',module='unit',source=dict(file=str(source),start_line=1,end_line=3)))
    model['edges']=[dict(**{'from':'unit','to':'unit.child'})]
    service.rtl_view=lambda _:model
    children=execute(service,session,'inspect','unit',None,'','')
    assert children['children'][0]['key']=='child'
    with pytest.raises(ValueError,match='direct child'):
        execute(service,session,'save','unit',overview,children['expected_revision'],children['source_revision'])
    overview['submodules']={'child':'One child used by the parent'}
    assert execute(service,session,'save','unit',overview,children['expected_revision'],children['source_revision'])['saved']
