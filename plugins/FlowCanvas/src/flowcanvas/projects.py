"""Local project discovery and explicit import, independent of model execution."""
import json
from pathlib import Path
import time
import threading
import uuid
import subprocess
import sys
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field


class ProjectRequest(BaseModel):
    path: str
    name: str = Field(default='', max_length=120)
    top: str = ''
    top_file: str = ''


class Projects:
    def __init__(self, state_root):
        self.lock=threading.RLock()
        self.file = state_root / 'projects' / 'state.json'
        try:
            self.state = json.loads(self.file.read_text())
        except (OSError, ValueError):
            self.state = {'current': None, 'recent': []}

        if self.state.get('current'):
            from .artifact_layout import migrate_artifacts
            migrate_artifacts(self.state['current']['path'])

    @property
    def current(self):
        return self.state.get('current')

    def directory(self, value):
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(400, '请选择存在的文件夹')
        return path

    def inspect(self, path):
        marker = path / '.flowcanvas' / 'project.json'
        if not marker.exists():
            return {'path': str(path), 'name': path.name, 'needs_import': True}
        try:
            data = json.loads(marker.read_text())
            if data.get('schema_version') != 1 or not data.get('id') or not isinstance(data.get('name'), str):
                raise ValueError()
        except (OSError, ValueError, AttributeError):
            raise HTTPException(400, '项目管理文件无效或版本不受支持，未覆盖原文件') from None
        return {**data, 'path': str(path), 'needs_import': False}

    def require_rtl(self, project, request=None):
        """Reject existing project metadata whose selected RTL definition is unavailable."""
        rtl=project.get('rtl') or {}
        source=(Path(project['path'])/rtl.get('top_file','')).resolve()
        if not rtl.get('top') or not rtl.get('top_file') or not source.is_relative_to(Path(project['path']).resolve()) or not source.is_file():
            raise HTTPException(422, '此项目缺少有效的 RTL 顶层设计，请选择包含 RTL 设计的文件夹')
        if request and ((request.top and request.top!=rtl['top']) or
                (request.top_file and (Path(project['path'])/request.top_file).resolve()!=source)):
            raise HTTPException(409, '项目的 RTL 顶层在首次创建时已固定，不能更改；请打开原项目')

    def activate(self, project):
        if project:
            from .artifact_layout import migrate_artifacts
            migrate_artifacts(project['path'])
        self.state['current'] = project
        if project:
            self.state['recent'] = [project] + [p for p in self.state['recent'] if p['path'] != project['path']][:11]
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2))
        temp.replace(self.file)
        callback=getattr(self,'on_activate',None)
        if callback:callback(project)
        return project


def parse_rtl(root, top='', top_file=''):
    try:
        process = subprocess.run([sys.executable, '-m', 'flowcanvas.rtl_scan'],
            input=json.dumps({'path': str(root), 'top': top, 'top_file': top_file}),
            capture_output=True, text=True, timeout=45)
        result = json.loads(process.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        raise HTTPException(422, 'RTL 扫描失败或超时，请缩小源码目录范围') from None
    if process.returncode or result.get('error'):
        raise HTTPException(422, result.get('error', 'RTL 解析失败'))
    return result


def routes(projects, sessions=None, workflow_root=None):
    router = APIRouter(prefix='/api/chat/projects')

    @router.get('')
    def state():
        return projects.state

    @router.get('/source')
    def source(path: str, media: bool = False):
        """Read a text file within an opened project, resolving symlinks before access."""
        roots = [Path(p['path']).resolve() for p in projects.state.get('recent', [])]
        if sessions is not None and workflow_root is not None:
            managed = Path(workflow_root).resolve()
            for session in sessions.values():
                for card in session.get('workflow_runs', []):
                    task = Path(card.get('root', '')).resolve()
                    workspace = Path(card.get('workspace', '')).resolve()
                    if task.parent == managed and workspace.is_relative_to(task) and workspace != task:
                        roots.append(workspace)
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            if not projects.current:
                raise HTTPException(404, '请先打开此文件所属的项目')
            candidate = Path(projects.current['path']) / candidate
        candidate = candidate.resolve()
        if not any(candidate.is_relative_to(root) for root in roots):
            raise HTTPException(403, '此文件不在已打开的项目中，请先打开所属项目')
        try:
            if not candidate.is_file():
                raise HTTPException(404, '文件不存在或已被移动')
            with candidate.open('rb') as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise HTTPException(413, '文件超过 2 MB，暂不支持预览')
            if media:
                suffix=candidate.suffix.lower()
                types={'.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg',
                       '.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp'}
                if suffix not in types:
                    raise HTTPException(415,'不支持的图片格式')
                if suffix=='.svg':
                    import xml.etree.ElementTree as ET
                    try:
                        if b'<!DOCTYPE' in raw.upper() or ET.fromstring(raw).tag!='{http://www.w3.org/2000/svg}svg':
                            raise ValueError()
                    except (ET.ParseError,ValueError):
                        raise HTTPException(415,'无效的 SVG 图片') from None
                elif not (raw.startswith(b'\x89PNG\r\n\x1a\n') if suffix=='.png' else
                          raw.startswith(b'\xff\xd8\xff') if suffix in {'.jpg','.jpeg'} else
                          raw.startswith((b'GIF87a',b'GIF89a')) if suffix=='.gif' else
                          raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'):
                    raise HTTPException(415,'图片内容与格式不符')
                return Response(raw,media_type=types[suffix],headers={
                    'X-Content-Type-Options':'nosniff',
                    'Content-Security-Policy':"sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"})
            if b'\x00' in raw:
                raise HTTPException(415, '此文件不是可预览的文本文件')
            return {'file': str(candidate), 'content': raw.decode('utf-8')}
        except UnicodeDecodeError:
            raise HTTPException(415, '此文件不是 UTF-8 文本') from None
        except OSError:
            raise HTTPException(403, '无法读取此文件') from None

    @router.get('/folders')
    def folders(path: str = ''):
        root = projects.directory(path or str(Path.home()))
        try:
            children = sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')), key=lambda p: p.name.lower())
        except OSError:
            raise HTTPException(403, '无法读取此文件夹') from None
        return {'path': str(root), 'parent': str(root.parent), 'is_project': (root/'.flowcanvas/project.json').is_file(),
                'folders': [{'name': p.name, 'path': str(p), 'is_project': (p/'.flowcanvas/project.json').is_file()} for p in children]}

    @router.post('/modules')
    def modules(request: ProjectRequest):
        return parse_rtl(projects.directory(request.path))

    @router.post('/dependencies')
    def dependencies(request: ProjectRequest):
        if not request.top:
            raise HTTPException(400, '请选择顶层模块')
        return parse_rtl(projects.directory(request.path), request.top, request.top_file)

    @router.post('/open')
    def open_project(request: ProjectRequest):
        root=projects.directory(request.path)
        project=projects.inspect(root)
        if project['needs_import']:
            inventory=parse_rtl(root)
            if not inventory.get('modules'):
                raise HTTPException(422, '所选文件夹未找到 RTL 模块，请选择包含 Verilog / SystemVerilog 设计的文件夹')
            return {**project,'modules':inventory['modules']}
        projects.require_rtl(project,request)
        return projects.activate(project)

    @router.post('/close')
    def close_project():
        projects.activate(None)
        return {'current': None}

    @router.post('/import')
    def import_project(request: ProjectRequest):
        root = projects.directory(request.path)
        project = projects.inspect(root)
        if not project['needs_import']:
            projects.require_rtl(project,request)
            return projects.activate(project)
        name = request.name.strip()
        if not name:
            raise HTTPException(400, '请输入项目名称')
        if not request.top:
            raise HTTPException(400, '请选择 RTL 顶层模块后打开项目')
        rtl = parse_rtl(root, request.top, request.top_file)
        if not rtl.get('files'):
            raise HTTPException(422, '所选文件夹缺少 RTL 设计源码')
        if not rtl['ready']:
            raise HTTPException(422, 'RTL 依赖尚未解析完整：' + '\n'.join(rtl['issues']))
        data = {'schema_version': 1, 'id': uuid.uuid4().hex, 'name': name, 'created_at': time.time()}
        if rtl:
            data['rtl'] = {**rtl, 'filelist': '.flowcanvas/rtl.f', 'filelist_base': '.'}
        # Recheck after parsing under one lock so racing first opens cannot rewrite a fixed filelist.
        with projects.lock:
            existing=projects.inspect(root)
            if not existing['needs_import']:
                projects.require_rtl(existing,request)
                return projects.activate(existing)
            marker = root / '.flowcanvas' / 'project.json'
            try:
                marker.parent.mkdir(exist_ok=True)
                if rtl:
                    lines = ['+incdir+' + json.dumps(p, ensure_ascii=False) for p in rtl['include_dirs']] + [json.dumps(p, ensure_ascii=False) for p in rtl['files']]
                    (marker.parent / 'rtl.f').write_text('\n'.join(lines) + '\n')
                with marker.open('x') as handle:
                    handle.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
            except FileExistsError:
                raise HTTPException(409, '项目已由其他操作导入，请重新打开') from None
            except OSError:
                raise HTTPException(403, '无法在该目录创建项目管理文件') from None
            return projects.activate({**data, 'path': str(root), 'needs_import': False})

    @router.post('/initialize')
    def initialize_project(request: ProjectRequest):
        """Create metadata only in an existing uninitialized folder; never create a new folder."""
        project=projects.inspect(projects.directory(request.path))
        if not project['needs_import']:
            raise HTTPException(409, '此文件夹已有项目，请使用“打开项目”')
        return import_project(request)

    return router
