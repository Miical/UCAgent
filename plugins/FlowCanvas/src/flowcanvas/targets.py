"""Versioned verification targets and explicit source-binding transactions."""
import argparse
from collections import Counter
import json
from pathlib import Path
import threading
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from .target_syntax import inventory

LOCK = threading.RLock()
SKILL = Path(__file__).parent / 'skills/verification-targets/SKILL.md'


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^bind_[0-9a-f]{32}$')
    file: str
    kind: str
    role: Literal['definition', 'stimulus', 'check', 'coverage', 'rtl'] = 'definition'
    fingerprint: str = ''


class Target(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^(fg|fp|tp)_[0-9a-f]{32}$')
    kind: Literal['group', 'point', 'testpoint']
    parent: str | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str = ''
    lifecycle: Literal['ready', 'creating', 'updating', 'deleting'] = 'ready'
    operation_status: Literal['running', 'paused'] = 'paused'
    operation_session: str = ''
    origin: Literal['manual', 'ai', 'scan'] = 'manual'
    bindings: list[Binding] = Field(default_factory=list)


class Definition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    targets: list[Target] = Field(default_factory=list)

    @model_validator(mode='after')
    def hierarchy(self):
        by_id = {n.id: n for n in self.targets}
        if len(by_id) != len(self.targets):
            raise ValueError('目标 ID 重复')
        ids = []
        for n in self.targets:
            expected = {'group': 'fg', 'point': 'fp', 'testpoint': 'tp'}[n.kind]
            if not n.id.startswith(expected + '_'):
                raise ValueError('目标类型和 ID 前缀不一致')
            if n.kind == 'group':
                if n.parent is not None:
                    raise ValueError('功能组不能有父节点')
            elif n.parent not in by_id or by_id[n.parent].kind != {'point': 'group', 'testpoint': 'point'}[n.kind]:
                raise ValueError('固定层级：功能组 → 功能点 → 测试点')
            ids.extend(b.id for b in n.bindings)
        if len(ids) != len(set(ids)):
            raise ValueError('一个绑定 ID 只能属于一个目标；其他位置请分配独立 ID')
        return self


def new_id(prefix):
    return prefix + '_' + uuid.uuid4().hex


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class Targets:
    definition_model = Definition
    target_model = Target
    prefixes = {'group':'fg','point':'fp','testpoint':'tp'}

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / '.flowcanvas/verification.json'

    def file(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path.suffix not in {'.v', '.sv', '.svh', '.py'} or not path.is_file():
            raise ValueError('绑定必须指向项目中的现有 .v/.sv/.svh/.py 源文件')
        return path

    def read(self):
        if not self.path.exists():
            return self.definition_model()
        return self.definition_model.model_validate_json(self.path.read_text())

    def save(self, definition, expected_revision):
        if self.read().revision != expected_revision:
            raise ValueError('定义已更新，请重新读取后重试')
        definition = self.definition_model.model_validate(definition.model_dump())
        for target in definition.targets:
            for binding in target.bindings:
                path = (self.root / binding.file).resolve()
                if not path.is_relative_to(self.root) or path.suffix not in {'.sv','.svh','.py'}:
                    raise ValueError('绑定路径必须是项目内源码')
        definition.revision = expected_revision + 1
        atomic(self.path, definition.model_dump())
        return definition

    def status(self):
        definition = self.read()
        files = {b.file for n in definition.targets for b in n.bindings}
        parsed, errors, counts = {}, {}, Counter()
        for file in sorted(files):
            try:
                parsed[file] = inventory(self.file(file))
                counts.update(parsed[file]['markers'])
            except (ValueError, OSError, SyntaxError) as exc:
                errors[file] = str(exc)
        reports = {}
        for target in definition.targets:
            records = []
            for binding in target.bindings:
                record = {'id': binding.id, 'file': binding.file, 'role': binding.role}
                if not (self.root / binding.file).is_file():
                    record.update(status='missing', message='绑定源码文件已移除')
                elif binding.file in errors:
                    record.update(status='unverified', message='源码无法解析或读取，绑定待校验')
                elif counts[binding.id] > 1:
                    record.update(status='conflict', message='源码标识重复')
                else:
                    found = [r for r in parsed[binding.file]['nodes'] if r['id'] == binding.id]
                    if len(found) != 1:
                        record.update(status='missing', message='标识或对应语法定义已移除')
                    elif found[0]['kind'] != binding.kind:
                        record.update(status='conflict', message='标识绑定的语法类型已改变')
                    else:
                        node = found[0]
                        record.update(status='bound' if node['fingerprint'] == binding.fingerprint else 'changed',
                                      line=node['line'], end_line=node['end_line'], symbol=node['name'], scope=node['scope'],
                                      fingerprint=node['fingerprint'])
                records.append(record)
            state = 'planned' if not records else next((v for v in ('conflict', 'unverified', 'missing', 'changed')
                                                       if any(r['status'] == v for r in records)), 'bound')
            reports[target.id] = {'status': state, 'bindings': records}
        extra = {}
        if self.path.name == 'verification.json':
            from .functional_coverage import project
            extra['coverage'] = project(self.root, definition, parsed, reports)
        return {**definition.model_dump(), **extra, 'reports': reports, 'errors': errors,
                'summary': dict(Counter(r['status'] for r in reports.values()))}

    def inspect(self, files):
        result = []
        for file in files:
            parsed = inventory(self.file(file))
            result.append({'file': file, 'nodes': parsed['nodes'], 'orphans': parsed['orphans']})
        return result

    def import_files(self, files, expected_revision):
        """Explicit import inserts comments and publishes only after reparsing succeeds."""
        with LOCK:
            definition = self.read()
            if definition.revision != expected_revision:
                raise ValueError('定义已更新，请重新读取')
            files = list(dict.fromkeys(files))
            from .target_import import validate_single_instance
            validate_single_instance(self.root, {file:inventory(self.file(file)) for file in files})
            existing = {b.id: n.id for n in definition.targets for b in n.bindings}
            binding_files = {b.id:b.file for n in definition.targets for b in n.bindings}
            original, staged, metadata = {}, {}, []
            for file in files:
                path = self.file(file)
                parsed = inventory(path)
                if any(c > 1 for c in Counter(parsed['markers']).values()) or parsed['orphans']:
                    raise ValueError(file + ': 存在重复或悬空标识，请先修复')
                text = parsed['text']
                original[path] = text
                by_start = {}
                for row in parsed['nodes']:
                    if row.get('excluded'):
                        continue
                    kind = {'CovergroupDeclaration': 'group', 'Coverpoint': 'point', 'CoverCross': 'point',
                            'CoverageBins': 'testpoint', 'BinsSelection': 'testpoint',
                            'ClassDef': 'group', 'FunctionDef': 'point', 'AsyncFunctionDef': 'point',
                            'Assert': 'testpoint'}.get(row['kind'])
                    if not kind:
                        continue
                    if row['id'] in existing:
                        if binding_files[row['id']] != file:
                            raise ValueError('标识已绑定其他文件，禁止自动重绑：'+row['id'])
                        by_start[row['start']] = existing[row['id']]
                        continue
                    parent = by_start.get(row['parent_start'])
                    if kind == 'point' and not parent and path.suffix == '.py':
                        # Module-level functions have an explicit, unbound planning group.
                        group = next((n for n in definition.targets if n.kind == 'group' and n.name == file), None)
                        if group is None:
                            group = Target(id=new_id('fg'), kind='group', name=file, origin='scan')
                            definition.targets.append(group)
                        parent = group.id
                    if kind != 'group' and not parent:
                        raise ValueError(file + ': 无法确定父级：' + row['name'])
                    bid = row['id'] or new_id('bind')
                    target = Target(id=new_id({'group':'fg','point':'fp','testpoint':'tp'}[kind]), kind=kind,
                        parent=None if kind == 'group' else parent, name=row['name'], origin='scan',
                        bindings=[Binding(id=bid, file=file, kind=row['kind'])])
                    if row['kind'] == 'CoverCross':
                        target.description = '组合覆盖定义；隐式组合未展开，命中情况需仿真报告。'
                    elif row['kind'] == 'CoverageBins' and '[' in text[row['start']:row['end']].split('=')[0]:
                        target.description = 'bin 数组定义模板，未展开为运行时各个 bin。'
                    definition.targets.append(target)
                    by_start[row['start']] = target.id
                    metadata.append((file, target, bid))
                    if not row['id']:
                        indent = re_indent(text, row['start'])
                        marker = ('#' if path.suffix == '.py' else '//') + ' @flowcanvas id=' + bid
                        # SV permits multiple declarations per line; split before the marked node.
                        insertion = ('' if not text[text.rfind('\n', 0, row['start'])+1:row['start']].strip() else '\n'+indent)
                        staged.setdefault(path, []).append((row['start'], insertion + marker + '\n' + indent))
            Definition.model_validate(definition.model_dump())
            written = {}
            try:
                for path, changes in staged.items():
                    if path.read_text() != original[path]:
                        raise ValueError('源码已变化，请重试')
                    text = original[path]
                    for offset, insertion in sorted(changes, reverse=True):
                        text = text[:offset] + insertion + text[offset:]
                    path.write_text(text)
                    written[path] = text
                reparsed = {file: inventory(self.file(file)) for file in files}
                counts = Counter(mid for p in reparsed.values() for mid in p['markers'])
                if any(c > 1 for c in counts.values()):
                    raise ValueError('跨文件标识重复')
                # Only acknowledge new bindings; old changed definitions remain pending.
                for file, target, bid in metadata:
                    matches = [r for r in reparsed[file]['nodes'] if r['id'] == bid]
                    if len(matches) != 1:
                        raise ValueError('新标识未绑定到唯一语法节点')
                    target.bindings[0].fingerprint = matches[0]['fingerprint']
                self.save(definition, expected_revision)
            except Exception:
                for path, text in written.items():
                    if path.read_text() == text:
                        path.write_text(original[path])
                raise
            return self.status()

    def create(self, kind, name, parent, expected_revision, description='', **details):
        allowed = {'condition', 'expected'} & self.target_model.model_fields.keys()
        if 'title' in self.target_model.model_fields:
            allowed.add('title')
        if set(details) - allowed:
            raise ValueError('不支持的字段：'+', '.join(sorted(set(details)-allowed)))
        with LOCK:
            data = self.read()
            row = self.target_model(id=new_id(self.prefixes[kind]), kind=kind,
                         parent=parent or None, name=name, description=description, **details)
            data.targets.append(row)
            self.save(data, expected_revision)
            return row.model_dump()

    def begin_create(self, kind, name, parent, revision, values, owner):
        with LOCK:
            data = self.read()
            if parent and any(n.id == parent and n.lifecycle == 'deleting' for n in data.targets):
                raise ValueError('父目标正在删除')
            if set(values) - {'description'}: raise ValueError('新建仅接受 description')
            row = Target(id=new_id(self.prefixes[kind]),kind=kind,name=name,parent=parent or None,
                         **values,origin='ai',lifecycle='creating',operation_status='running',operation_session=owner)
            data.targets.append(row)
            saved = self.save(data,revision)
            return {**row.model_dump(),'revision':saved.revision}

    def operation(self, target_id, operation, revision, owner, cascade=False, planned=False):
        """Finish a bound implementation or explicitly retain an unbound plan."""
        with LOCK:
            data = self.read()
            row = next((n for n in data.targets if n.id == target_id),None)
            if row is None: raise ValueError('目标不存在')
            affected = {target_id}
            if operation == 'begin_delete' or (operation == 'resume_operation' and row.lifecycle == 'deleting'):
                while True:
                    children = {n.id for n in data.targets if n.parent in affected}
                    if children <= affected: break
                    affected.update(children)
                if len(affected)>1 and not cascade: raise ValueError('需要明确 values.cascade=true')
            if operation == 'finish_create':
                if row.lifecycle != 'creating': raise ValueError('目标不在创建中')
                if not row.description.strip():
                    raise ValueError('A description is required before finish_create')
                if planned:
                    if row.bindings:
                        raise ValueError('Planned completion requires no bindings; validate existing bindings instead')
                elif self.status()['reports'][target_id]['status'] != 'bound':
                    raise ValueError('Valid source binding required; for an unimplemented plan use finish_create with values.planned=true')
                row.lifecycle='ready';row.operation_status='paused';row.operation_session=''
            else:
                if operation == 'resume_operation' and row.lifecycle == 'ready': raise ValueError('没有待恢复操作')
                for item in data.targets:
                    if item.id in affected:
                        if operation == 'begin_delete': item.lifecycle='deleting'
                        item.operation_status='running';item.operation_session=owner
            saved=self.save(data,revision)
            return {**row.model_dump(),'revision':saved.revision}

    def reconcile_operations(self, sessions):
        from .subagents import execution_active
        with LOCK:
            data=self.read();changed=False
            for row in data.targets:
                if row.lifecycle!='ready' and row.operation_status=='running':
                    owner=sessions.get(row.operation_session)
                    if not owner or not execution_active(owner):row.operation_status='paused';changed=True
            if changed:self.save(data,data.revision)

    def delete(self, target_id, expected_revision, cascade=False):
        with LOCK:
            data = self.read()
            if not any(n.id == target_id for n in data.targets):
                raise ValueError('目标不存在')
            ids = {target_id}
            while True:
                descendants = {n.id for n in data.targets if n.parent in ids}
                if descendants <= ids: break
                ids.update(descendants)
            if len(ids) > 1 and not cascade:
                raise ValueError('目标含子目标，需要明确 values.cascade=true')
            data.targets = [n for n in data.targets if n.id not in ids]
            saved = self.save(data, expected_revision)
            return {'deleted': sorted(ids), 'revision': saved.revision}

    def require_edit(self, target_id, owner):
        """Require AI writes to own a running creation or modification operation."""
        row = next((item for item in self.read().targets if item.id == target_id), None)
        if row is None:
            raise ValueError("Target does not exist")
        if owner:
            if (row.lifecycle not in {"creating", "updating"} or
                    row.operation_status != "running" or row.operation_session != owner):
                raise ValueError("Call begin_update with the latest revision before update/bind/accept; "
                                 "resume your paused operation with resume_operation.")
        elif row.operation_session or row.lifecycle == "deleting":
            raise ValueError("Card is owned by an AI operation; finish or resume it before manual editing")

    def edit_operation(self, target_id, operation, revision, owner):
        """Persist modification state before edits and clear it only after validation."""
        with LOCK:
            data = self.read()
            row = next((item for item in data.targets if item.id == target_id), None)
            if row is None:
                raise ValueError("Target does not exist")
            if operation == "begin_update":
                if row.lifecycle != "ready":
                    raise ValueError("Card already has an operation; resume_operation instead")
                row.lifecycle = "updating"
                row.operation_status = "running"
                row.operation_session = owner
            else:
                if (row.lifecycle != "updating" or row.operation_session != owner or
                        row.operation_status != "running"):
                    raise ValueError("finish_update requires your running modification operation")
                if row.bindings and self.status()["reports"][target_id]["status"] != "bound":
                    raise ValueError("Resolve source bindings before finish_update; read and verify changed source")
                row.lifecycle = "ready"
                row.operation_status = "paused"
                row.operation_session = ""
            saved = self.save(data, revision)
            return {**row.model_dump(), "revision": saved.revision}

    def update(self, target_id, values, expected_revision):
        with LOCK:
            data = self.read()
            allowed = {'name', 'description'} | ({'condition', 'expected'} & self.target_model.model_fields.keys())
            if 'title' in self.target_model.model_fields:
                allowed.add('title')
            if set(values) - allowed:
                raise ValueError('不支持的字段：'+', '.join(sorted(set(values)-allowed)))
            target = next((n for n in data.targets if n.id == target_id), None)
            if not target:
                raise ValueError('目标不存在')
            replacement = self.target_model.model_validate({**target.model_dump(), **values})
            data.targets[data.targets.index(target)] = replacement
            self.save(data, expected_revision)
            return replacement.model_dump()

    def accept(self, target_id, expected_revision, fingerprints):
        with LOCK:
            report = self.status()
            data = self.read()
            target = next(n for n in data.targets if n.id == target_id)
            for binding in target.bindings:
                row = next(r for r in report['reports'][target_id]['bindings'] if r['id']==binding.id)
                if row['status'] not in {'bound','changed'} or fingerprints.get(binding.id) != row.get('fingerprint'):
                    raise ValueError('代码已变化或绑定无效，不能确认')
                binding.fingerprint = row['fingerprint']
            self.save(data, expected_revision)
            return self.status()

    def bind(self, target_id, values, expected_revision):
        with LOCK:
            data = self.read()
            if data.revision != expected_revision:
                raise ValueError('定义已更新，请重新读取')
            target = next(n for n in data.targets if n.id == target_id)
            file = values['file']
            path = self.file(file)
            parsed = inventory(path)
            matches = [n for n in parsed['nodes'] if n['start'] == values['start']
                       and n['fingerprint'] == values['fingerprint']]
            if len(matches) != 1:
                raise ValueError('源码已变化，请重新 inspect')
            node = matches[0]
            bid = node['id'] or new_id('bind')
            if any(b.id == bid for n in data.targets for b in n.bindings):
                raise ValueError('此位置已经绑定，请通过原目标维护')
            text = parsed['text']
            changed = False
            try:
                if not node['id']:
                    indent = re_indent(text, node['start'])
                    line_prefix = text[text.rfind('\n',0,node['start'])+1:node['start']]
                    prefix = '' if not line_prefix.strip() else '\n'+indent
                    comment = ('#' if path.suffix == '.py' else '//')+' @flowcanvas id='+bid
                    if path.read_text() != text:
                        raise ValueError('源码已变化')
                    path.write_text(text[:node['start']]+prefix+comment+'\n'+indent+text[node['start']:])
                    changed = True
                nodes = [n for n in inventory(path)['nodes'] if n['id']==bid]
                if len(nodes)!=1:
                    raise ValueError('标识无法绑定到唯一节点')
                target.bindings.append(Binding(id=bid,file=file,kind=node['kind'],
                    role=values.get('role','definition'),fingerprint=nodes[0]['fingerprint']))
                self.save(data,expected_revision)
            except Exception:
                if changed:path.write_text(text)
                raise
            return self.status()

    def source(self, binding_id):
        report = self.status()
        row = next((b for n in report['reports'].values() for b in n['bindings'] if b['id']==binding_id), None)
        if not row or 'line' not in row:
            raise ValueError('绑定尚未校验成功，不能使用旧行号跳转')
        lines = self.file(row['file']).read_text().splitlines()
        return {**row, 'content':'\n'.join(lines[row['line']-1:row['end_line']]),
                'file_content':'\n'.join(lines)}


def re_indent(text, offset):
    import re
    return re.match(r'[ \t]*', text[text.rfind('\n', 0, offset)+1:]).group()


class Request(BaseModel):
    operation: str = 'read'
    expected_revision: int = 0
    files: list[str] = Field(default_factory=list, max_length=100)
    kind: Literal['group','point','testpoint'] = 'group'
    name: str = ''
    parent: str = ''
    target_id: str = ''
    binding_id: str = ''
    values: dict = Field(default_factory=dict)


def execute(root, request, owner=""):
    """Require an owned operation before AI mutations, atomically with the write."""
    with LOCK:
        r = request if isinstance(request, Request) else Request.model_validate(request)
        store = Targets(root)
        if r.operation in {"begin_update", "finish_update"}:
            return store.edit_operation(r.target_id, r.operation, r.expected_revision, owner)
        if r.operation in {"update", "bind", "accept"}:
            store.require_edit(r.target_id, owner)
        return _execute(root, r, owner)


def _execute(root, request, owner=""):
    """Dispatch a request after the public transaction boundary validates ownership."""
    store = Targets(root)
    r = request if isinstance(request, Request) else Request.model_validate(request)
    if r.operation == 'read': return store.status()
    if r.operation == 'inspect': return store.inspect(r.files)
    if r.operation == 'import': return store.import_files(r.files, r.expected_revision)
    if r.operation == 'begin_create': return store.begin_create(r.kind,r.name,r.parent,r.expected_revision,r.values,owner)
    if r.operation in {'begin_delete','finish_create','resume_operation'}: return store.operation(r.target_id,r.operation,r.expected_revision,owner,r.values.get('cascade') is True,r.values.get('planned') is True)
    if r.operation == 'create': return store.create(r.kind,r.name,r.parent,r.expected_revision,**r.values)
    if r.operation == 'delete': return store.delete(r.target_id,r.expected_revision,r.values.get('cascade') is True)
    if r.operation == 'update': return store.update(r.target_id,r.values,r.expected_revision)
    if r.operation == 'accept': return store.accept(r.target_id,r.expected_revision,r.values)
    if r.operation == 'bind': return store.bind(r.target_id,r.values,r.expected_revision)
    if r.operation == 'source': return store.source(r.binding_id)
    if r.operation == 'guide': return {'skill':SKILL.read_text()+'\nAI 新建先 begin_create 注册占位，再 update/bind 同一 ID，最后 finish_create；尚无验证实现时明确使用 values={planned:true} 完成待实现规划，不伪造绑定。AI 删除首先 read 获取 revision 并定位指定 ID，立即 begin_delete（含子目标需 cascade=true）登记卡片；登记成功后才检查源码、共享引用和删除范围，最后清理代码并 delete。不要检查完才登记。中断后 resume_operation 恢复同一 ID。均需最新 expected_revision。'+'\n删除使用 delete(target_id, expected_revision)。含子目标时必须明确 values.cascade=true；仅删除登记定义，源码须单独核对处理，保留共享代码和历史产物。', 'schema':Definition.model_json_schema()}
    raise ValueError('未知操作')


def routes(projects, sessions=None):
    router = APIRouter(prefix='/api/chat/projects')
    def root(identity):
        project = next((p for p in projects.state['recent'] if p['id']==identity),None)
        if not project: raise HTTPException(404,'项目不存在')
        if sessions is not None: Targets(project['path']).reconcile_operations(sessions)
        return project['path']
    @router.get('/{identity}/targets/revision')
    def revision(identity:str):
        try:
            from .functional_coverage import revision as coverage_revision
            return {'revision':Targets(root(identity)).read().revision, 'coverage_revision':coverage_revision(root(identity))}
        except (ValueError,OSError) as exc: raise HTTPException(422,str(exc)) from None
    @router.get('/{identity}/targets')
    def read(identity:str):
        try: return Targets(root(identity)).status()
        except (ValueError,OSError) as exc: raise HTTPException(422,str(exc)) from None
    @router.post('/{identity}/targets')
    def action(identity:str, request:Request):
        try: return execute(root(identity),request)
        except (ValueError,OSError,KeyError,StopIteration,SyntaxError) as exc: raise HTTPException(422,str(exc)) from None
    return router


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',required=True)
    parser.add_argument('--request',required=True,help='JSON request file')
    args=parser.parse_args()
    print(json.dumps(execute(args.project,json.loads(Path(args.request).read_text())),ensure_ascii=False,indent=2))
