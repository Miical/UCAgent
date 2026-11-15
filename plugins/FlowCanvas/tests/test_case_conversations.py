"""Case ownership persists independently of UI state; view references cannot cross cases."""
import asyncio
import json
from pathlib import Path
import pytest
import httpx2
from fastapi import FastAPI, HTTPException
from flowcanvas.chat import ChatService, Message
from flowcanvas.case_conversations import routes, CaseView, view_snapshot
from flowcanvas.testcases import Cases
from test_case_import import make_case


def setup(tmp_path):
    """Create a real service projection with two source-bound cases and no model process."""
    root=tmp_path/'project';root.mkdir()
    identity,call=make_case(root,'def test_start():\n    assert True\n')
    second=call('apply',count=1)['ids'][0]
    service=ChatService(tmp_path/'state')
    project=dict(id='project',name='fixture',path=str(root))
    service.projects.state['recent']=[project];service.projects.state['current']='project'
    # Routes resolve explicit project IDs, not whichever project is selected in the shell.
    app=FastAPI();app.include_router(routes(service))
    return service,app,root,identity,second


@pytest.mark.asyncio
async def test_lazy_unique_binding_and_restart(tmp_path):
    """Read-only opens create nothing; concurrent materializations converge and survive restart."""
    service,app,root,first,second=setup(tmp_path)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
        base='/api/chat/projects/project/testcases/'
        for _ in range(2):assert (await client.get(base+first+'/conversation')).json()=={'session':None}
        assert service.sessions=={}
        results=await asyncio.gather(*(client.post(base+first+'/conversation',json=dict(backend='codex')) for _ in range(3)))
        ids={r.json()['id'] for r in results};assert len(ids)==1
        assert len(service.sessions)==1
        first_session=results[0].json()
        assert first_session['thread'] is None and first_session['items']==[]
        assert first_session['project_root']==str(root)
        other=(await client.post(base+second+'/conversation',json=dict(backend='codex'))).json()
        assert other['id'] not in ids
        assert (await client.post(base+'unknown/conversation',json={})).status_code==404
        assert (await client.get('/api/chat/projects/missing/testcases/'+first+'/conversation')).status_code==404
    restored=ChatService(tmp_path/'state')
    assert restored.sessions[first_session['id']]['case_binding']==dict(project_id='project',case_id=first)
    assert len(restored.sessions)==2


@pytest.mark.asyncio
async def test_multiple_scoped_histories_are_idempotent_and_durable(tmp_path):
    """Explicit new chats keep old histories and retries never create duplicates."""
    service,app,root,first,second=setup(tmp_path)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
        url='/api/chat/projects/project/testcases/'+first+'/conversation'
        original=(await client.post(url,json={})).json()
        draft='a'*32
        results=await asyncio.gather(*(client.post(url,json={'draft_id':draft}) for _ in range(3)))
        created=results[0].json()
        assert all(result.json()['id']==created['id'] for result in results)
        assert created['id']!=original['id'] and len(service.sessions)==2
        assert created['case_binding']==original['case_binding']
        assert (await client.get(url)).json()['session']['id']==created['id']
        assert (await client.post(url,json={})).json()['id']==created['id']
        service.sessions[created['id']]['archived']=True;service.save(service.sessions[created['id']])
        assert (await client.get(url)).json()['session']['id']==original['id']
        assert (await client.post(url,json={'draft_id':draft})).json()['id']==created['id']
        assert (await client.post(url,json={'draft_id':'../invalid'})).status_code==422
    restored=ChatService(tmp_path/'state')
    assert len(restored.sessions)==2 and restored.sessions[created['id']]['case_binding']==original['case_binding']


def test_context_freezes_authoritative_card_and_rejects_foreign_artifacts(tmp_path):
    """A source/run selection is validated against ownership before it becomes prompt context."""
    service,app,root,first,second=setup(tmp_path)
    session=service.create('codex',project=dict(path=str(root)),case_binding=dict(project_id='project',case_id=first))
    binding=Cases(root).read().targets[0].bindings[0]
    snapshot=view_snapshot(service,session,CaseView(tab='source',binding_id=binding.id,line=2))
    assert snapshot['case_id']==first and snapshot['source']['file']=='test_start.py'
    assert 'file_content' not in snapshot['source']
    assert snapshot['view']['line']==2
    with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='source',binding_id='foreign'))
    folder=root/'.flowcanvas/test-runs';folder.mkdir()
    run_id='a'*32;metadata=folder/(run_id+'.json')
    metadata.write_text(json.dumps(dict(id=run_id,case_id=second,status='completed')))
    with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='runs',run_id=run_id))
    metadata.write_text(json.dumps(dict(id=run_id,case_id=first,status='completed',build_id='build-A')))
    result=view_snapshot(service,session,CaseView(tab='runs',run_id=run_id))
    assert result['run']['build_id']=='build-A'
    metadata.write_text(json.dumps(dict(id=run_id,case_id=first,build_id='build-B')))
    assert result['run']['build_id']=='build-A'
    directory=folder/run_id;directory.mkdir();(directory/'wave.vcd').write_text('$end')
    assert view_snapshot(service,session,CaseView(tab='wave',run_id=run_id,waveform='wave.vcd'))['view']['waveform']=='wave.vcd'
    outside=root/'secret.vcd';outside.write_text('secret');(directory/'link.vcd').symlink_to(outside)
    for name in ('../../../secret.vcd','link.vcd'):
        with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='wave',run_id=run_id,waveform=name))
    with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='wave',waveform='wave.vcd'))
    with pytest.raises(HTTPException):view_snapshot(service,dict(project_root=str(root)),CaseView())
    with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='source',binding_id=binding.id,line=900))


@pytest.mark.asyncio
async def test_message_queue_freezes_view_and_ignores_forged_snapshot(tmp_path):
    """Exercise the real send route without a model turn; queued messages keep their original context."""
    from unittest.mock import AsyncMock
    from flowcanvas.chat import routes as chat_routes
    service,app,root,first,second=setup(tmp_path)
    service.models=AsyncMock(return_value=[dict(id='fixture-model',efforts=['low'])])
    app.include_router(chat_routes(service))
    session=service.create('codex',project=dict(path=str(root)),case_binding=dict(project_id='project',case_id=first))
    session['status']='running'
    binding=Cases(root).read().targets[0].bindings[0]
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
        payload=dict(text='Explain this source',model='fixture-model',effort='low',
                     case_view=dict(tab='source',binding_id=binding.id,line=2),view_context=dict(case_id=second))
        response=await client.post('/api/chat/sessions/'+session['id']+'/messages',json=payload)
        assert response.status_code==202,response.text
        saved=response.json()['queue'][0]['message']
        assert saved['view_context']['case_id']==first
        assert saved['view_context']['view']['tab']=='source'
        assert first in saved['quote'] and 'reference only' in saved['quote']
        payload['case_view']=dict(tab='overview')
        await client.post('/api/chat/sessions/'+session['id']+'/messages',json=payload)
        assert session['queue'][0]['message']['view_context']['view']['tab']=='source'
        assert session['queue'][1]['message']['view_context']['view']['tab']=='overview'
        ordinary=service.create('codex',project=dict(path=str(root)))
        assert (await client.post('/api/chat/sessions/'+ordinary['id']+'/messages',json=payload)).status_code==409
