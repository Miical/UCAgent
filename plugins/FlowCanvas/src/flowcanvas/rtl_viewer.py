"""Adapt project filelists to the installed RTL parser and serve viewer data."""
from importlib.metadata import PackageNotFoundError, version
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import threading
from fastapi import APIRouter, HTTPException


def routes(service):
    router = APIRouter(prefix='/api/chat/projects')
    lock = threading.Lock()

    @router.get('/{identity}/rtl-view')
    def view(identity: str):
        project = next((p for p in service.projects.state['recent'] if p['id'] == identity), None)
        if not project:
            raise HTTPException(404, '项目不存在，请重新打开项目')
        root = Path(project['path']).resolve()
        try:
            config = json.loads((root / '.flowcanvas/project.json').read_text())
            rtl = config.get('rtl') or {}
            if not rtl.get('top') or not rtl.get('filelist'):
                raise HTTPException(400, '项目尚未配置顶层模块和 filelist')
            filelist = (root / rtl['filelist']).resolve()
            if not filelist.is_relative_to(root):
                raise HTTPException(400, 'filelist 必须位于项目目录中')
            content = filelist.read_text()
            files = []
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith(('#', '//', '+incdir+')):
                    continue
                parts = shlex.split(line)
                if len(parts) != 1 or parts[0].startswith(('+', '-')):
                    raise HTTPException(400, '当前原版查看器不支持此 filelist 指令：' + line)
                path = (root / parts[0]).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise HTTPException(400, 'RTL 文件不存在或超出项目目录：' + parts[0])
                if '#' in str(path):
                    raise HTTPException(400, '原版查看器暂不支持路径包含 # 的源码')
                files.append(path)
            if not files:
                raise HTTPException(400, 'filelist 中没有源码文件')
            try:
                parser_version = version('rtl-buddy-sch')
            except PackageNotFoundError:
                raise HTTPException(503, '缺少 RTL 解析器，请安装 rtl-buddy-sch==0.15.0') from None
            digest = hashlib.sha256((content + rtl['top'] + str(root)).encode())
            digest.update(('rtl-buddy-sch:' + parser_version).encode())
            for path in files:
                digest.update(path.read_bytes())
            key = digest.hexdigest()
            cache = service.root / 'rtl_views' / identity / key
            with lock:
                output = cache / 'view.json'
                if output.is_file():
                    return json.loads(output.read_text())
                cache.mkdir(parents=True, exist_ok=True)
                # Upstream accepts only plain paths relative to its own filelist.
                # Keep the user's canonical filelist unchanged; supply absolute paths.
                normalized = cache / 'upstream.f'
                normalized.write_text('\n'.join(str(p) for p in files) + '\n')
                env = os.environ.copy()
                env['PATH'] = str(service.root / 'tools/bin') + os.pathsep + env.get('PATH', '')
                if not shutil.which('verible-verilog-syntax', path=env['PATH']):
                    raise HTTPException(503, '缺少 Verible：请将 verible-verilog-syntax 安装到 PATH 或用户状态目录 tools/bin')
                process = subprocess.run([sys.executable, '-m', 'rtl_buddy_view', '--top', rtl['top'],
                    '--filelist', str(normalized), '--format', 'json', '--output', str(output)],
                    cwd=root, env=env, capture_output=True, text=True, timeout=60)
                if process.returncode:
                    output.unlink(missing_ok=True)
                    raise HTTPException(422, 'RTL 解析失败：' + (process.stderr or process.stdout)[-2500:])
                return json.loads(output.read_text())
        except subprocess.TimeoutExpired:
            raise HTTPException(504, 'RTL 解析超时') from None
        except (OSError, ValueError) as exc:
            raise HTTPException(422, '无法读取 RTL 项目：' + str(exc)) from None

    return router
