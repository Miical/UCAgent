"""Bug tabs retain scoped histories and keep simulated demo records distinct from real evidence."""
import pytest
import httpx2
from fastapi import FastAPI, HTTPException
from flowcanvas.bugs import Bugs, execute
from flowcanvas.chat import ChatService
from flowcanvas.case_conversations import routes, CaseView, view_snapshot


@pytest.mark.asyncio
async def test_bug_tabs_and_demo_provenance(tmp_path):
    """Open demo details without model execution and reject unsupported runtime or foreign-source context."""
    root=tmp_path/'project';root.mkdir()
    first=execute(root,dict(operation='create',name='Demo missing data',expected_revision=0,values=dict(example=True,description='Hypothetical UI example',condition='Hypothetical burst',expected='All bytes retained',actual='Assumed missing byte; not reproduced',evidence='No simulation evidence')))
    second=execute(root,dict(operation='create',name='Demo stale flag',expected_revision=Bugs(root).read().revision,values=dict(example=True)))
    service=ChatService(tmp_path/'state');project=dict(id='p',path=str(root));service.projects.state={'current':project,'recent':[project]};app=FastAPI();app.include_router(routes(service))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
        url='/api/chat/projects/p/bugs/'+first['id']+'/conversation'
        assert (await client.get(url)).json()=={'session':None}
        session=(await client.post(url,json={})).json();assert session['case_binding']['case_id']==first['id']
        another=(await client.post(url,json={'draft_id':'e'*32})).json();assert another['id']!=session['id']
        other=(await client.post('/api/chat/projects/p/bugs/'+second['id']+'/conversation',json={})).json();assert other['case_binding']!=session['case_binding']
        assert (await client.get('/api/chat/projects/p/bugs/bug_'+'f'*32+'/conversation')).status_code==404
    context=view_snapshot(service,session,CaseView())
    assert context['entity_type']=='bug' and context['bug']['example'] is True and context['bug']['status']=='unverified'
    assert context['bug']['actual']=='Assumed missing byte; not reproduced'
    with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='runs'))
    with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='source',binding_id='foreign'))
    with pytest.raises(ValueError):execute(root,dict(operation='update',target_id=first['id'],expected_revision=Bugs(root).read().revision,values=dict(status='confirmed',evidence='Demo is not evidence')))
    assert ChatService(tmp_path/'state').sessions[session['id']]['case_binding']==session['case_binding']
