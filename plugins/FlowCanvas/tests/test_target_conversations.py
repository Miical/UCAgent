"""Verification-target tabs own independent histories and validate their source context."""
import pytest
import httpx2
from fastapi import FastAPI, HTTPException
from flowcanvas.chat import ChatService
from flowcanvas.case_conversations import routes, CaseView, view_snapshot
from flowcanvas.targets import Targets, execute


@pytest.mark.asyncio
async def test_target_history_and_source_scope(tmp_path):
    """Bind target histories lazily, preserve new-chat ownership and reject foreign source/run context."""
    root=tmp_path/'project';root.mkdir();(root/'checks.py').write_text('def first(x):\n    return x == 1\n\ndef second(x):\n    return x == 2\n')
    def call(operation,**values):
        """Exercise the real target transaction boundary at its latest revision."""
        return execute(root,dict(operation=operation,expected_revision=Targets(root).read().revision,**values),'fixture')
    ids=call('apply',count=2)['ids'];call('begin_modify',ids=ids)
    call('update',cards=[dict(id=identity,kind='group',name='Target '+str(index),description='Check actual output',bindings=[dict(file='checks.py',line=line,line_count=2,role='check')]) for index,(identity,line) in enumerate(zip(ids,[1,4]))]);call('end_modify',ids=ids)
    service=ChatService(tmp_path/'state');project=dict(id='p',path=str(root));service.projects.state={'recent':[project],'current':project}
    app=FastAPI();app.include_router(routes(service));url='/api/chat/projects/p/targets/'+ids[0]+'/conversation'
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get(url)).json()=={'session':None}
        session=(await client.post(url,json={})).json();other=(await client.post(url,json={'draft_id':'d'*32})).json()
        assert session['id']!=other['id'] and session['case_binding']==other['case_binding']
        assert (await client.get(url)).json()['session']['id']==other['id']
    snapshot=view_snapshot(service,session,CaseView())
    assert snapshot['entity_type']=='target' and snapshot['case_id']==ids[0]
    targets=Targets(root).read().targets
    source=view_snapshot(service,session,CaseView(tab='source',binding_id=targets[0].bindings[0].id))
    assert 'first' in source['source']['content'] and 'file_content' not in source['source']
    with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='source',binding_id=targets[1].bindings[0].id))
    with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='runs'))
    assert ChatService(tmp_path/'state').sessions[other['id']]['case_binding']==session['case_binding']
