"""Opening a project requires an existing folder with an actual RTL module."""
from pathlib import Path
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flowcanvas.projects import Projects, routes


def test_folder_entry_opens_projects_or_prepares_first_initialization(tmp_path):
    """Open existing metadata directly or request a manual top selection without creating folders."""
    project=tmp_path/'design';project.mkdir()
    store=Projects(tmp_path/'state');app=FastAPI();app.include_router(routes(store))
    with TestClient(app) as client:
        request=dict(path=str(project),name='Design')
        assert client.post('/api/chat/projects/open',json=request).status_code==422
        assert client.post('/api/chat/projects/import',json=request).status_code==400
        assert not (project/'.flowcanvas').exists()
        assert client.post('/api/chat/projects/create',json=dict(path=str(tmp_path),name='new')).status_code==404
        assert not (tmp_path/'new').exists()
        (project/'checks.sv').write_text('class checks; endclass\n')
        assert client.post('/api/chat/projects/open',json=request).status_code==422
        (project/'top.sv').write_text('module top(input logic a,output logic y); assign y=a; endmodule\n')
        response=client.post('/api/chat/projects/open',json=request)
        assert response.status_code==200,response.text
        assert response.json()['needs_import']
        assert [module['name'] for module in response.json()['modules']]==['top']
        assert not (project/'.flowcanvas').exists()
        # Even a folder containing exactly one module still requires an explicit user selection.
        assert client.post('/api/chat/projects/import',json=request).status_code==400
        response=client.post('/api/chat/projects/initialize',json={**request,'top':'top','top_file':'top.sv'})
        assert response.status_code==200,response.text
        identity=response.json()['id']
        assert client.post('/api/chat/projects/initialize',json={**request,'top':'top','top_file':'top.sv'}).status_code==409
        assert (project/'.flowcanvas/rtl.f').is_file()
        assert client.post('/api/chat/projects/open',json=request).json()['id']==identity
        assert client.post('/api/chat/projects/close').status_code==200
        assert client.get('/api/chat/projects').json()['current'] is None
        assert (project/'.flowcanvas/project.json').is_file()
        assert client.post('/api/chat/projects/open',json=request).json()['id']==identity
        metadata=(project/'.flowcanvas/project.json').read_bytes()
        filelist=(project/'.flowcanvas/rtl.f').read_bytes()
        (project/'other.sv').write_text('module other; endmodule\n')
        for endpoint in ('open','import'):
            changed={**request,'top':'other','top_file':'other.sv'}
            assert client.post('/api/chat/projects/'+endpoint,json=changed).status_code==409
            assert client.post('/api/chat/projects/'+endpoint,json={**request,'top_file':'other.sv'}).status_code==409
        assert (project/'.flowcanvas/project.json').read_bytes()==metadata
        assert (project/'.flowcanvas/rtl.f').read_bytes()==filelist
        alias=tmp_path/'alias';alias.symlink_to(project,target_is_directory=True)
        assert client.post('/api/chat/projects/open',json={'path':str(alias)}).json()['id']==identity
        (project/'top.sv').unlink()
        assert client.post('/api/chat/projects/open',json=request).status_code==422
        assert client.post('/api/chat/projects/import',json=request).status_code==422


def test_project_start_ui_has_one_shared_folder_entry():
    """One folder entry keeps manual top selection for first initialization."""
    root=Path(__file__).parents[1]/'src/flowcanvas/web'
    script=(root/'projects.js').read_text();html=(root/'index.html').read_text()
    assert 'project-new' not in script+html
    assert "candidate.create" not in script
    assert '打开 RTL 项目' in script and '打开项目' in script and '创建项目' in script
    assert "api('/open',{path});if(result.needs_import)" in script
    assert 'project-entry-tabs' not in html+script
    assert "api('/initialize'" in script
    assert "rtlReady" in script
    assert 'project-leave' in html and "$('project-leave').onclick=closeProject" in script
    assert '请选择 RTL 顶层模块' in script


def test_folder_browser_marks_projects_and_retains_navigation_context(tmp_path):
    """Directory navigation reports only folders and identifies existing project metadata."""
    parent=tmp_path/'folders';parent.mkdir()
    existing=parent/'existing';(existing/'.flowcanvas').mkdir(parents=True)
    (existing/'.flowcanvas/project.json').write_text('{}')
    (parent/'plain').mkdir();(parent/'.hidden').mkdir();(parent/'notes.txt').write_text('text')
    store=Projects(tmp_path/'state');app=FastAPI();app.include_router(routes(store))
    with TestClient(app) as client:
        response=client.get('/api/chat/projects/folders',params={'path':str(parent)})
        assert response.status_code==200
        data=response.json();assert data['path']==str(parent) and data['parent']==str(tmp_path)
        assert not data['is_project']
        assert [(item['name'],item['is_project']) for item in data['folders']]==[('existing',True),('plain',False)]
        assert client.get('/api/chat/projects/folders',params={'path':str(existing)}).json()['is_project']
        assert client.get('/api/chat/projects/folders',params={'path':str(parent/'missing')}).status_code==400
