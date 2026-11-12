"""Shared case-card lifecycle and explicit test execution entries."""
import asyncio
import json
import pytest
from flowcanvas.testcases import Cases, execute, configure
from flowcanvas.test_runner import TestRuns as Runs


@pytest.fixture
def api(tmp_path):
    """Call the actual case tool with current revisions."""
    def call(operation,owner='alice',**kw):
        return execute(tmp_path,dict(operation=operation,expected_revision=Cases(tmp_path).read().revision,**kw),owner)
    return tmp_path,call


def test_batch_registration_binding_and_running_lock(api):
    """All cases require owned edits, complete metadata and explicit definitions."""
    root,call=api
    (root/'test_examples.py').write_text('def test_a():\n    assert True\n\ndef test_b():\n    assert True\n')
    ids=call('apply',count=2)['ids']
    assert all(c.lifecycle=='draft' for c in Cases(root).read().targets)
    with pytest.raises(ValueError): call('update',cards=[{'id':ids[0],'title':'提前'}])
    call('begin_modify',ids=ids)
    call('update',cards=[dict(id=i,name=name,title='用例',description='断言通过',runner={'command':'python -m pytest {case}','description':'Fixture'},bindings=[dict(file='test_examples.py',line=line,line_count=2)])
                         for i,name,line in zip(ids,('test_a','test_b'),(1,4))])
    call('end_modify',ids=ids)
    folder=root/'.flowcanvas/test-runs';folder.mkdir()
    run=folder/'active.json';run.write_text(json.dumps(dict(case_id=ids[0],status='running')))
    for action in ('update','delete'):
        with pytest.raises(ValueError,match='running'):call('begin_modify',ids=ids,action=action)
    assert all(c.lifecycle=='ready' for c in Cases(root).read().targets)
    run.unlink()
    call('begin_modify',ids=ids,action='delete')
    with pytest.raises(ValueError):call('end_modify',ids=ids,owner='other')
    call('end_modify',ids=ids)
    assert not Cases(root).read().targets


def test_legacy_operations_are_rejected_and_title_required(api):
    """Case cards expose the same five operations, without implicit legacy import."""
    root,call=api
    for op in ('read','guide','create','import','discover','bind','accept','configure','begin_delete'):
        with pytest.raises(ValueError):call(op)
    identity=call('apply',count=1)['ids'][0];call('begin_modify',ids=[identity])
    with pytest.raises(ValueError):call('end_modify',ids=[identity])


@pytest.mark.asyncio
async def test_real_command_uses_explicit_entry_and_rejects_stale_source(api):
    """Execute one isolated subprocess and refuse changed source on a second start."""
    root,call=api
    (root/'test_demo.py').write_text('def test_ok():\n    assert True\n')
    identity=call('apply',count=1)['ids'][0];call('begin_modify',ids=[identity])
    call('update',cards=[dict(id=identity,name='test_ok',title='运行入口',description='运行断言',runner={'command':'printf "%s" {case}','description':'Print entry'},bindings=[dict(file='test_demo.py',line=1,line_count=2)])])
    call('end_modify',ids=[identity])
    runner=Runs()
    try:
        run=await runner.start(root,identity)
        assert run['entry']==str(root/'test_demo.py')+'::test_ok'
        await asyncio.gather(*runner.tasks.values())
        assert runner.output(root,run['id'])['run']['exit_code']==0
        (root/'test_demo.py').write_text((root/'test_demo.py').read_text().replace('True','False'))
        with pytest.raises(ValueError,match='changed'):await runner.start(root,identity)
    finally:
        await runner.close()


def test_paused_or_foreign_case_cannot_be_changed(api):
    """Resume the same operation explicitly and retain ownership across pauses."""
    root,call=api
    identity=call('apply',count=1)['ids'][0]
    call('begin_modify',ids=[identity])
    with pytest.raises(ValueError):call('update',owner='bob',cards=[{'id':identity,'title':'wrong'}])
    Cases(root).reconcile_operations({})
    before=Cases(root).read().revision
    with pytest.raises(ValueError):call('update',cards=[{'id':identity,'title':'paused'}])
    assert Cases(root).read().revision==before
    call('begin_modify',ids=[identity])
    call('update',cards=[{'id':identity,'title':'恢复'}])
    assert Cases(root).read().targets[0].title=='恢复'


def test_case_batch_is_atomic(api):
    """An invalid card in a batch cannot partially update a valid card."""
    root,call=api
    ids=call('apply',count=2)['ids'];call('begin_modify',ids=ids)
    before=Cases(root).path.read_bytes()
    with pytest.raises(ValueError):call('update',cards=[{'id':ids[0],'title':'new'},{'id':ids[1],'parent':ids[0]}])
    assert Cases(root).path.read_bytes()==before


def test_case_http_contract(api):
    """The browser endpoint rejects old writes and exposes only the shared operations."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from flowcanvas.testcases import routes
    root,call=api
    app=FastAPI();app.include_router(routes(SimpleNamespace(state={'recent':[{'id':'p','path':str(root)}]})))
    client=TestClient(app);url='/api/chat/projects/p/testcases'
    assert client.post(url,json={'operation':'create','name':'old'}).status_code==422
    response=client.post(url,json={'operation':'apply','count':2})
    assert response.status_code==200
    assert len(response.json()['ids'])==2
    result=client.post(url,json={'operation':'inspect'}).json()
    assert result['request_schema']['properties']['operation']['enum']==['inspect','apply','begin_modify','update','end_modify']
    assert 'configuration_schema' in result


@pytest.mark.asyncio
async def test_two_cases_run_their_own_commands(api):
    """The selected case determines the command; no project command is consulted."""
    root,call=api
    (root/'test_pair.py').write_text('def test_a():\n    pass\ndef test_b():\n    pass\n')
    ids=call('apply',count=2)['ids'];call('begin_modify',ids=ids)
    call('update',cards=[dict(id=i,name=name,title=label,description='Distinct command',
         runner={'command':'printf "'+label+':%s" {case}','description':label},
         bindings=[{'file':'test_pair.py','line':line,'line_count':2}])
         for i,name,label,line in zip(ids,('test_a','test_b'),('A','B'),(1,3))])
    call('end_modify',ids=ids)
    runner=Runs()
    try:
        for identity,label in zip(ids,('A','B')):
            run=await runner.start(root,identity)
            await asyncio.gather(*runner.tasks.values())
            result=runner.output(root,run['id'])
            assert label+':'+run['entry'] in result['text']
            assert result['run']['description']==label
    finally:
        await runner.close()
    with pytest.raises(ValueError):
        configure(root,{'runner':{'command':'echo {case}','description':'removed'}},Cases(root).read().revision)
