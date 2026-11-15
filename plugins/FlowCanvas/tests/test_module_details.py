"""One design tab resolves real instances and keeps its conversation shared without a model call."""
import hashlib
import json
import pytest
from fastapi import FastAPI, HTTPException
import httpx2
from flowcanvas.chat import ChatService
from flowcanvas.module_details import routes as module_routes
from flowcanvas.case_conversations import routes, view_snapshot, CaseView


@pytest.mark.asyncio
async def test_module_identity_source_and_conversation(tmp_path):
    """Switch two instances within one design conversation, retaining validated source context."""
    root=tmp_path/'project';root.mkdir();source=root/'unit.sv';source.write_text('module unit;\nendmodule\n')
    service=ChatService(tmp_path/'state');project=dict(id='p',path=str(root),name='fixture')
    service.projects.state={'current':project,'recent':[project]}
    service.rtl_view=lambda identity:dict(top='top.a',nodes=[dict(id=name,module='unit',source=dict(file=str(source),start_line=1,end_line=2)) for name in ('top.a','top.b')])
    app=FastAPI();app.include_router(module_routes(service));app.include_router(routes(service))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
        first=(await client.get('/api/chat/projects/p/module',params={'instance':'top.a'})).json()
        second=(await client.get('/api/chat/projects/p/module',params={'instance':'top.b'})).json()
        assert first['module']['id']==second['module']['id']=='module_design'
        metadata=root/'.flowcanvas';metadata.mkdir()
        overview=metadata/'module-overviews.json'
        entry=dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),purpose=dict(text='Buffered storage',line=1))
        overview.write_text(json.dumps({'unit.sv::unit':entry}))
        populated=(await client.get('/api/chat/projects/p/module',params={'instance':'top.a'})).json()
        assert populated['module']['overview']==entry
        entry['source_sha256']='stale'
        overview.write_text(json.dumps({'unit.sv::unit':entry}))
        assert (await client.get('/api/chat/projects/p/module',params={'instance':'top.a'})).json()['module']['overview'] is None
        overview.write_text('{invalid')
        assert (await client.get('/api/chat/projects/p/module',params={'instance':'top.a'})).status_code==200
        identity=first['module']['id'];url='/api/chat/projects/p/modules/'+identity+'/conversation'
        assert (await client.get(url)).json()=={'session':None}
        session=(await client.post(url,json={'backend':'codex'})).json()
        assert (await client.post(url,json={'backend':'codex'})).json()['id']==session['id']
        other=(await client.post('/api/chat/projects/p/modules/'+second['module']['id']+'/conversation',json={'backend':'codex'})).json()
        assert session['id']==other['id']
        another=(await client.post(url,json={'backend':'codex','draft_id':'b'*32})).json()
        assert another['id']!=session['id'] and another['case_binding']==session['case_binding']
        snapshot=view_snapshot(service,session,CaseView(tab='source'))
        assert snapshot['entity_type']=='design' and snapshot['instance']=='top.a'
        assert snapshot['source']['file']=='unit.sv'
        assert 'file_content' not in snapshot['source']
        selected=view_snapshot(service,session,CaseView(tab='source',module_instance='top.b'))
        assert selected['instance']=='top.b'
        assert view_snapshot(service,session,CaseView(tab='overview',module_instance='top.b'))['instance']=='top.b'
        coverage=view_snapshot(service,session,CaseView(tab='coverage',module_instance='top.b',coverage_metric='branch'))
        assert coverage['instance']=='top.b' and coverage['coverage']['metric']=='branch'
        assert 'current_source' in coverage and 'source' not in coverage
        with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='runs'))
        assert (await client.get('/api/chat/projects/p/module',params={'instance':'../missing'})).status_code==404
