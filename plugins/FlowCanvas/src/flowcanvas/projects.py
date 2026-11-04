"""Local project discovery and explicit import, independent of model execution."""
import json
from pathlib import Path
import time
import uuid
import subprocess
import sys
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


class ProjectRequest(BaseModel):
    path: str
    name: str = Field(default='', max_length=120)
    top: str = ''
    top_file: str = ''


class Projects:
    def __init__(self, state_root):
        self.file = state_root / 'projects' / 'state.json'
        try:
            self.state = json.loads(self.file.read_text())
        except (OSError, ValueError):
            self.state = {'current': None, 'recent': []}

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

    def activate(self, project):
        self.state['current'] = project
        if project:
            self.state['recent'] = [project] + [p for p in self.state['recent'] if p['path'] != project['path']][:11]
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2))
        temp.replace(self.file)
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


def routes(projects):
    router = APIRouter(prefix='/api/chat/projects')

    @router.get('')
    def state():
        return projects.state

    @router.get('/source')
    def source(path: str):
        """Read a text file within an opened project, resolving symlinks before access."""
        roots = [Path(p['path']).resolve() for p in projects.state.get('recent', [])]
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
        return {'path': str(root), 'parent': str(root.parent), 'folders': [{'name': p.name, 'path': str(p)} for p in children]}

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
        project = projects.inspect(projects.directory(request.path))
        return project if project['needs_import'] else projects.activate(project)

    @router.post('/close')
    def close_project():
        projects.activate(None)
        return {'current': None}

    @router.post('/create')
    def create_project(request: ProjectRequest):
        parent = projects.directory(request.path)
        name = request.name.strip()
        if not name or name in {'.', '..'} or '/' in name or '\\' in name or '\x00' in name:
            raise HTTPException(400, '请输入有效的项目文件夹名称')
        root = parent / name
        try:
            root.mkdir()
        except FileExistsError:
            raise HTTPException(409, '同名文件夹已存在，请换一个名称或使用打开项目') from None
        except OSError:
            raise HTTPException(403, '无法在所选位置创建项目目录') from None
        return import_project(ProjectRequest(path=str(root), name=name))

    @router.post('/import')
    def import_project(request: ProjectRequest):
        root = projects.directory(request.path)
        project = projects.inspect(root)
        if not project['needs_import']:
            return projects.activate(project)
        name = request.name.strip()
        if not name:
            raise HTTPException(400, '请输入项目名称')
        rtl = parse_rtl(root, request.top, request.top_file) if request.top else None
        if not request.top and (any(root.rglob('*.sv')) or any(root.rglob('*.v'))):
            raise HTTPException(400, '请选择顶层模块后导入')
        if rtl and not rtl['ready']:
            raise HTTPException(422, 'RTL 依赖尚未解析完整：' + '\n'.join(rtl['issues']))
        data = {'schema_version': 1, 'id': uuid.uuid4().hex, 'name': name, 'created_at': time.time()}
        if rtl:
            data['rtl'] = {**rtl, 'filelist': '.flowcanvas/rtl.f', 'filelist_base': '.'}
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

    return router
