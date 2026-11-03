"""Syntax-based RTL inventory and conservative source dependency closure."""
import json
import os
from pathlib import Path
import re
import sys

SKIP = {'.git', '.flowcanvas', '.venv', 'venv', 'node_modules', '__pycache__', 'obj_dir'}


def scan(root):
    root = Path(root).resolve()
    import pyslang as slang
    from pyslang.syntax import SyntaxTree
    from pyslang.parsing import PreprocessorOptions
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not Path(directory, d).is_symlink())
        for name in sorted(names):
            path = Path(directory, name)
            if path.suffix.lower() in {'.v', '.sv', '.vh', '.svh'} and not path.is_symlink():
                files.append(path)
    if len(files) > 10000:
        raise ValueError('源码文件超过 10000 个，请选择更具体的 RTL 目录')
    includes = sorted({str(p.parent) for p in files})
    options = PreprocessorOptions()
    options.additionalIncludePaths = includes
    units, sources = {}, {}
    for path in files:
        relative = str(path.relative_to(root))
        if path.stat().st_size > 8 * 1024 * 1024:
            sources[relative] = {'dependencies': [], 'includes': [], 'diagnostic': '文件超过 8 MiB，未解析'}
            continue
        tree = SyntaxTree.fromFile(str(path), slang.SourceManager(), slang.Bag([options]))
        sm = tree.sourceManager
        dependencies, headers = set(), set()
        def visit(node):
            kind = str(node.kind).split('.')[-1]
            if kind in {'ModuleDeclaration', 'InterfaceDeclaration', 'PackageDeclaration'}:
                token = node.header.name
                source = Path(str(sm.getFileName(token.location))).resolve()
                if not source.is_relative_to(root):
                    return
                source = str(source.relative_to(root))
                name = token.valueText
                key = (source, name, kind, sm.getLineNumber(token.location))
                if key not in units or not path.suffix.lower() in {'.vh', '.svh'}:
                    units[key] = {'name': name, 'file': source, 'line': key[3], 'kind': kind, 'compile_file': relative}
            if kind == 'HierarchyInstantiation':
                dependencies.add(node.type.valueText)
            elif kind == 'PackageImportItem':
                dependencies.add(node.package.valueText)
            elif kind == 'ScopedName':
                name = str(node.left).strip().split('::')[0]
                if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', name) and name not in {'std', 'local', 'this', 'super'}:
                    dependencies.add(name)
            if hasattr(node, 'getFirstToken'):
                source = Path(str(sm.getFileName(node.getFirstToken().location))).resolve()
                if source != path and source.is_file() and source.is_relative_to(root):
                    headers.add(str(source.relative_to(root)))
        tree.root.visit(visit)
        for buffer in sm.getAllBuffers():
            included = Path(str(sm.getFileName(slang.SourceLocation(buffer, 0)))).resolve()
            if included != path and included.is_file() and included.is_relative_to(root):
                headers.add(str(included.relative_to(root)))
        diagnostic = slang.DiagnosticEngine.reportAll(sm, [d for d in tree.diagnostics if d.isError()])
        sources[relative] = {'dependencies': sorted(dependencies), 'includes': sorted(headers), 'diagnostic': diagnostic}
    return {'modules': sorted(units.values(), key=lambda x: (x['name'], x['file'])), 'sources': sources, 'include_dirs': [str(Path(p).relative_to(root)) for p in includes]}


def resolve(index, top, top_file):
    units = index['modules']
    choices = [u for u in units if u['name'] == top and u['kind'] == 'ModuleDeclaration' and (not top_file or u['file'] == top_file)]
    if len(choices) != 1:
        raise ValueError('请选择唯一的顶层模块及其文件')
    by_name = {}
    for unit in units:
        by_name.setdefault(unit['name'], []).append(unit)
    ordered, headers, visiting, visited, issues = [], set(), set(), set(), []
    def walk(file):
        if file in visited or file in visiting:
            return
        visiting.add(file)
        source = index['sources'].get(file)
        if source is None:
            issues.append('未找到源码：' + file)
            return
        if source['diagnostic']:
            issues.append(file + ': ' + source['diagnostic'][:1500])
        headers.update(source['includes'])
        local = {u['name'] for u in units if u['file'] == file or u['file'] in source['includes']}
        for name in source['dependencies']:
            if name in local:
                continue
            candidates = by_name.get(name, [])
            if len(candidates) != 1:
                issues.append(('缺少依赖：' if not candidates else '依赖定义重名：') + name + ('（' + '、'.join(u['file'] for u in candidates) + '）' if candidates else ''))
                continue
            unit = candidates[0]
            walk(unit['compile_file'] if unit['file'].endswith(('.vh', '.svh')) else unit['file'])
        visiting.remove(file)
        visited.add(file)
        ordered.append(file)
    selected = choices[0]
    walk(selected['compile_file'] if selected['file'].endswith(('.vh', '.svh')) else selected['file'])
    return {'top': top, 'top_file': selected['file'], 'files': ordered, 'headers': sorted(headers),
            'include_dirs': index['include_dirs'], 'issues': list(dict.fromkeys(issues)), 'ready': not issues}


if __name__ == '__main__':
    request = json.load(sys.stdin)
    root = Path(request['path']).resolve()
    try:
        index = scan(root)
        result = resolve(index, request['top'], request.get('top_file', '')) if request.get('top') else {'modules': [u for u in index['modules'] if u['kind'] == 'ModuleDeclaration']}
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
