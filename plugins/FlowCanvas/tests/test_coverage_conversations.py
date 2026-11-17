"""Coverage conversations share metric ownership while validating each selected build source."""
import hashlib
from types import SimpleNamespace

import httpx2
import pytest
from fastapi import FastAPI, HTTPException
from flowcanvas.chat import ChatService
from flowcanvas.case_conversations import CaseView, routes, view_snapshot


def setup(tmp_path,monkeypatch):
    """Provide two real build sources and a bounded parsed hierarchy without starting a model."""
    from flowcanvas import coverage_sources, module_details
    root=tmp_path/'project';root.mkdir()
    content='module alpha;\nwire a;\nendmodule\nmodule beta;\nwire b;\nendmodule\n'
    (root/'dut.sv').write_text(content)
    sha=hashlib.sha256(content.encode()).hexdigest()
    instances=[dict(path='top.'+name,module=name,files=[dict(path='dut.sv',sha256=sha)]) for name in ('alpha','beta')]
    state=dict(build_id='build-A',report=dict(instances=instances,code_metrics=dict(toggle=dict(dut=dict(items=[dict(hierarchy=row['path'],module=row['module'],file='dut.sv',sha256=sha) for row in instances])))))
    monkeypatch.setattr(coverage_sources,'state',lambda path:state)
    def module_record(service,project_id,identity='',instance=''):
        """Reject selections outside the fixture's authoritative module hierarchy."""
        row=next((row for row in instances if row['path']==instance),None)
        if not row:raise HTTPException(404,'Module instance not found')
        return {},SimpleNamespace(instance=instance,name=row['module']),{}
    monkeypatch.setattr(module_details,'module_record',module_record)
    service=ChatService(tmp_path/'state');project=dict(id='p',name='fixture',path=str(root))
    service.projects.state={'recent':[project],'current':project}
    app=FastAPI();app.include_router(routes(service))
    return service,project,app,root,content,sha


@pytest.mark.asyncio
async def test_metric_history_is_lazy_shared_and_durable(tmp_path,monkeypatch):
    """Opening sources creates no history; metric scopes reuse sessions and survive restart."""
    service,project,app,root,content,sha=setup(tmp_path,monkeypatch)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
        base='/api/chat/projects/p/coverage/'
        assert (await client.get(base+'coverage_line/conversation')).json()=={'session':None}
        assert service.sessions=={}
        first=(await client.post(base+'coverage_line/conversation',json={})).json()
        assert (await client.post(base+'coverage_line/conversation',json={})).json()['id']==first['id']
        other=(await client.post(base+'coverage_toggle/conversation',json={})).json()
        assert first['id']!=other['id']
        assert first['case_binding']==dict(project_id='p',case_id='coverage_line')
        assert first['thread'] is None
        assert (await client.get(base+'coverage_unknown/conversation')).status_code==404
    assert ChatService(tmp_path/'state').sessions[first['id']]['case_binding']==first['case_binding']


def test_metric_context_follows_modules_and_rejects_foreign_evidence(tmp_path,monkeypatch):
    """One history follows module selection, but metric, file, hierarchy and hash remain guarded."""
    service,project,app,root,content,sha=setup(tmp_path,monkeypatch)
    session=service.create('codex',project=project,case_binding=dict(project_id='p',case_id='coverage_line'))
    def snapshot(**selection):
        """Build a source selection for this shared metric scope."""
        return view_snapshot(service,session,CaseView(**dict(tab='source',module_instance='top.alpha',coverage_metric='line',coverage_file='dut.sv',**selection)))
    first=snapshot()
    second=view_snapshot(service,session,CaseView(tab='source',module_instance='top.beta',coverage_metric='line',coverage_file='dut.sv'))
    assert first['case_id']==second['case_id']=='coverage_line'
    assert first['instance']=='top.alpha' and second['instance']=='top.beta'
    assert 'module alpha' in first['source']['content'] and 'module beta' not in first['source']['content']
    assert first['source']['sha256']==sha and first['source']['build_id']=='build-A'
    for view in [CaseView(tab='source',module_instance='top.alpha',coverage_metric='toggle'),CaseView(tab='source',module_instance='foreign',coverage_metric='line'),CaseView(tab='source',module_instance='top.alpha',coverage_metric='line',coverage_file='../secret'),CaseView(tab='source',module_instance='top.alpha',coverage_metric='line',line=999),CaseView(tab='runs')]:
        with pytest.raises(HTTPException):view_snapshot(service,session,view)
    (root/'dut.sv').write_text('changed')
    with pytest.raises(HTTPException):snapshot()
    cache=root/'output/flowcanvas/coverage/sources';cache.mkdir(parents=True);(cache/(sha+'.sv')).write_text(content)
    assert snapshot()['source']['sha256']==sha
