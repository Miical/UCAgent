"""Conservative source checks before importing single-instance goal definitions."""
import ast
from pathlib import Path
import re

IGNORED = {'.git','.flowcanvas','.venv','venv','node_modules','build','dist','__pycache__','output'}

def sources(root, suffixes):
    for path in Path(root).rglob('*'):
        if any(part in IGNORED for part in path.relative_to(root).parts): continue
        if path.is_file() and path.suffix in suffixes:
            yield path, path.read_text()

def strip_comments(text):
    return re.sub(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*',
                  lambda m: m[0] if m[0].startswith('"') else ' ', text)

def validate_single_instance(root, imported):
    """Reject known reuse and unresolved SV construction before modifying files."""
    sv = '\n'.join(strip_comments(text) for _,text in sources(root,{'.sv','.svh'}))
    py = []
    repeated_calls=set()
    for path,text in sources(root,{'.py'}):
        try:
            tree=ast.parse(text);py.extend(ast.walk(tree))
            for node in ast.walk(tree):
                if isinstance(node,(ast.For,ast.AsyncFor,ast.While,ast.ListComp,ast.SetComp,ast.DictComp,ast.GeneratorExp)):
                    repeated_calls.update(id(child) for child in ast.walk(node) if isinstance(child,ast.Call))
        except SyntaxError: raise ValueError(f'{path}: 无法核对调用关系，请先修复语法')
    for file, parsed in imported.items():
        if Path(file).suffix != '.py':
            owners = set()
            for node in parsed['nodes']:
                if node['kind'] != 'CovergroupDeclaration': continue
                classes=[n for n in parsed['nodes'] if n['kind']=='ClassDeclaration' and n['start']<node['start'] and n['end']>=node['end']]
                if len(classes)!=1:
                    raise ValueError(file+': 覆盖定义必须由一个明确的独立组件承载；请 AI 拆分后导入')
                owner=classes[0]['name'];owners.add(owner)
                body=strip_comments(parsed['text'][classes[0]['start']:classes[0]['end']])
                if len(re.findall(r'\b'+re.escape(node['name'])+r'\s*=\s*new\b',body))!=1:
                    raise ValueError(file+': '+node['name']+' 必须只构造一次')
            for owner in owners:
                calls=re.findall(r'\b'+re.escape(owner)+r'\s*::\s*type_id\s*::\s*create\s*\(',sv)
                dynamic=False
                for match in re.finditer(r'\b'+re.escape(owner)+r'\s*::\s*type_id\s*::\s*create\s*\(',sv):
                    prefix=sv[:match.start()]
                    method=max(prefix.rfind('function '),prefix.rfind('task '))
                    dynamic=dynamic or bool(re.search(r'\b(for|foreach|while|repeat|forever)\b',prefix[method:])) or not re.match(r'\s*"[^"]+"\s*,',sv[match.end():])
                inherited=re.search(r'\bextends\s+'+re.escape(owner)+r'\b',sv)
                if len(calls)!=1 or inherited or dynamic:
                    raise ValueError(f'{file}: {owner} 检出 {len(calls)} 个 factory 创建点或继承复用；必须复制成独立组件、分配独立 ID 并修改调用位置。无法确定唯一实例时不允许导入。')
        else:
            tree=ast.parse(parsed['text'])
            for node in ast.walk(tree):
                if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): continue
                if not any(isinstance(n,ast.Assert) for n in ast.walk(node)): continue
                calls=[n for n in py if isinstance(n,ast.Call) and
                       (isinstance(n.func,ast.Name) and n.func.id==node.name or isinstance(n.func,ast.Attribute) and n.func.attr==node.name)]
                parametrized=any('parametrize' in ast.unparse(d) for d in node.decorator_list)
                if len(calls)>1 or parametrized or any(id(call) in repeated_calls for call in calls):
                    raise ValueError(f'{file}: {node.name} 的检查被多处调用或参数化复用，请复制独立检查入口及 ID 后导入')
