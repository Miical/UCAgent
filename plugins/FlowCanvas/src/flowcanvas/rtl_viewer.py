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
            digest.update(('rtl-buddy-sch:' + parser_version + ':definition-sources-v3-lf-inputs').encode())
            for path in files:
                digest.update(path.read_bytes())
            key = digest.hexdigest()
            cache = service.root / 'rtl_views' / identity / key
            with lock:
                output = cache / 'view.json'
                if output.is_file():
                    return json.loads(output.read_text())
                cache.mkdir(parents=True, exist_ok=True)
                # Verible offsets refer to original bytes, but upstream read_text()
                # normalizes CRLF before slicing expressions. Parse private LF snapshots
                # so tokens, offsets and snippets agree; leave project files untouched.
                source_root=cache/'sources';parser_files=[]
                for path in files:
                    snapshot=source_root/path.relative_to(root);snapshot.parent.mkdir(parents=True,exist_ok=True)
                    snapshot.write_bytes(path.read_bytes().replace(b'\r\n',b'\n').replace(b'\r',b'\n'))
                    parser_files.append(snapshot)
                normalized = cache / 'upstream.f'
                normalized.write_text('\n'.join(str(p) for p in parser_files) + '\n')
                env = os.environ.copy()
                env['PATH'] = str(service.root / 'tools/bin') + os.pathsep + env.get('PATH', '')
                if not shutil.which('verible-verilog-syntax', path=env['PATH']):
                    raise HTTPException(503, '缺少 Verible：请将 verible-verilog-syntax 安装到 PATH 或用户状态目录 tools/bin')
                raw_output=cache/'upstream-view.json'
                process = subprocess.run([sys.executable, '-m', 'rtl_buddy_view', '--top', rtl['top'],
                    '--filelist', str(normalized), '--format', 'json', '--output', str(raw_output)],
                    cwd=root, env=env, capture_output=True, text=True, timeout=60)
                if process.returncode:
                    output.unlink(missing_ok=True)
                    raise HTTPException(422, 'RTL 解析失败：' + (process.stderr or process.stdout)[-2500:])
                model=json.loads(raw_output.read_text())
                from urllib.parse import quote
                def restore_paths(value):
                    """Restore original source paths in structured locations and embedded graph links."""
                    if isinstance(value,dict):return {key:restore_paths(item) for key,item in value.items()}
                    if isinstance(value,list):return [restore_paths(item) for item in value]
                    if isinstance(value,str):return value.replace(str(source_root),str(root)).replace(quote(str(source_root),safe=''),quote(str(root),safe=''))
                    return value
                model=restore_paths(model)
                # Upstream child sources describe instantiation sites. Resolve definitions
                # from the configured RTL files, without guessing from module names or regexes.
                import pyslang as slang
                from pyslang.syntax import SyntaxTree
                from pyslang.parsing import PreprocessorOptions
                options=PreprocessorOptions();options.additionalIncludePaths=sorted({str(path.parent) for path in files})
                definitions={};instantiations={}
                for path in files:
                    tree=SyntaxTree.fromFile(str(path),slang.SourceManager(),slang.Bag([options]));manager=tree.sourceManager
                    def visit(node):
                        """Resolve module declarations and instance statements from source tokens."""
                        kind=str(node.kind).split('.')[-1]
                        if kind not in {'ModuleDeclaration','HierarchyInstantiation'}:return
                        first,last=node.getFirstToken(),node.getLastToken()
                        source_path=Path(str(manager.getFileName(first.location))).resolve()
                        if not source_path.is_relative_to(root):return
                        source=dict(file=str(source_path),start_line=manager.getLineNumber(first.location),end_line=manager.getLineNumber(last.location))
                        location=(source['file'],source['start_line'])
                        if kind=='ModuleDeclaration':
                            source['decl_line']=manager.getLineNumber(node.header.name.location)
                            definitions.setdefault(node.header.name.valueText,{})[location]=source
                            return
                        parent=node.parent
                        while parent is not None and str(parent.kind).split('.')[-1]!='ModuleDeclaration':parent=parent.parent
                        if parent is None:return
                        for instance in node.instances:
                            if str(getattr(instance,'kind','')).split('.')[-1]!='HierarchicalInstance':continue
                            key=(parent.header.name.valueText,node.type.valueText,instance.decl.name.valueText)
                            instantiations.setdefault(key,{})[location]=source
                    tree.root.visit(visit)
                nodes={node['id']:node for node in model.get('nodes',[])}
                for node in nodes.values():
                    matches=list(definitions.get(node['module'],{}).values())
                    if node['id']!=model['top']:
                        parent=node['id'].rsplit('.',1)[0]
                        while parent not in nodes and '.' in parent:parent=parent.rsplit('.',1)[0]
                        owner=nodes.get(parent,{}).get('module')
                        name=node.get('instance_name') or node['id'].rsplit('.',1)[-1]
                        name=name.split('[',1)[0]
                        sites=list(instantiations.get((owner,node['module'],name),{}).values())
                        node['instance_source']=sites[0] if len(sites)==1 else None
                    # Missing or ambiguous definitions must stay unavailable, never point to a parent.
                    node['source']=matches[0] if len(matches)==1 else None
                pending=output.with_suffix('.tmp');pending.write_text(json.dumps(model,ensure_ascii=False));pending.replace(output)
                return model
        except subprocess.TimeoutExpired:
            raise HTTPException(504, 'RTL 解析超时') from None
        except (OSError, ValueError) as exc:
            raise HTTPException(422, '无法读取 RTL 项目：' + str(exc)) from None

    service.rtl_view = view
    return router
