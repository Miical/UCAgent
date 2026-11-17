"""Global/project conversation ownership persists independently of the currently displayed scope."""
import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flowcanvas.chat import ChatService, routes


def project(root, identity):
    """Prepare a real initialized folder without running RTL tools or a model."""
    root.mkdir();(root/'.flowcanvas').mkdir()
    value=dict(id=identity,path=str(root),name=identity,schema_version=1,created_at=0)
    (root/'.flowcanvas/project.json').write_text(json.dumps(value))
    return value


def test_scope_switching_preserves_global_and_project_histories(tmp_path):
    """New projects start empty; closing and reopening restores each scope's own conversations."""
    service=ChatService(tmp_path/'state');a=project(tmp_path/'a','a');b=project(tmp_path/'b','b')
    global_chat=service.create();global_chat['title']='Global';service.save(global_chat)
    service.projects.activate(a);main=service.create()
    scoped=service.create(project=a,case_binding=dict(project_id='a',case_id='tc_example'))
    for session in (main,scoped):
        assert service.session_path(session)==Path(a['path'])/'.flowcanvas/conversations'/(session['id']+'.json')
        assert not (service.root/(session['id']+'.json')).exists()
    assert (service.root/(global_chat['id']+'.json')).is_file()
    app=FastAPI();app.include_router(routes(service))
    with TestClient(app) as client:
        assert {row['id'] for row in client.get('/api/chat/sessions').json()}=={main['id'],scoped['id']}
        service.projects.activate(b)
        assert client.get('/api/chat/sessions').json()==[]
        service.projects.activate(None)
        assert [row['id'] for row in client.get('/api/chat/sessions').json()]==[global_chat['id']]
        service.projects.activate(a)
        assert {row['id'] for row in client.get('/api/chat/sessions').json()}=={main['id'],scoped['id']}
        response=client.post('/api/chat/sessions/'+scoped['id']+'/delete')
        assert response.status_code==200,response.text
        assert not (Path(a['path'])/'.flowcanvas/conversations'/(scoped['id']+'.json')).exists()
        assert (service.root/(global_chat['id']+'.json')).is_file()
    restored=ChatService(service.root)
    assert main['id'] in restored.sessions and scoped['id'] not in restored.sessions
    assert global_chat['id'] in restored.sessions
    assert restored.session_path(restored.sessions[main['id']]).parent==Path(a['path'])/'.flowcanvas/conversations'


def test_switching_does_not_redirect_active_writes_or_reload_live_objects(tmp_path):
    """An in-flight project conversation keeps saving to its original folder after scope changes."""
    service=ChatService(tmp_path/'state');a=project(tmp_path/'a','a');b=project(tmp_path/'b','b')
    service.projects.activate(a);session=service.create();session['status']='running';service.save(session)
    service.projects.activate(b);session['title']='Still A';service.save(session)
    assert json.loads(service.session_path(session).read_text())['title']=='Still A'
    assert not (Path(b['path'])/'.flowcanvas/conversations').exists()
    service.projects.activate(a)
    assert service.sessions[session['id']] is session
    assert session['status']=='running'


def test_project_not_in_recent_list_loads_its_folder_history_on_open(tmp_path):
    """Opening an existing project discovers its conversations without a global history copy."""
    a=project(tmp_path/'a','a');writer=ChatService(tmp_path/'writer-state')
    writer.projects.activate(a);session=writer.create()
    reader=ChatService(tmp_path/'reader-state')
    assert session['id'] not in reader.sessions
    reader.projects.activate(a)
    assert reader.sessions[session['id']]['project_id']=='a'
    assert not (reader.root/(session['id']+'.json')).exists()
