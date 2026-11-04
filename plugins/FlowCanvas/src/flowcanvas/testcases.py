"""Explicit test discovery and stable source bindings, independent of run results."""
import ast
import json
from collections import Counter
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .targets import Targets, Binding, LOCK, Request as TargetRequest, new_id, re_indent
from .target_syntax import inventory


class Request(TargetRequest):
    kind: Literal['case'] = 'case'
    files: list[str] = Field(default_factory=list, max_length=2000)


class TestCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^tc_[0-9a-f]{32}$')
    kind: Literal['case'] = 'case'
    parent: None = None
    name: str = Field(min_length=1, max_length=200, description='代码中的测试类名或函数名，不作为可读标题')
    title: str = Field(default='', max_length=200, description='用例的可读标题；新建和导入必须填写，概括测试场景')
    description: str = ''
    lifecycle: Literal['ready', 'creating', 'deleting'] = 'ready'
    operation_status: Literal['running', 'paused'] = 'paused'
    operation_session: str = ''
    origin: Literal['manual', 'ai', 'scan'] = 'manual'
    bindings: list[Binding] = Field(default_factory=list)


class RunTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command: str = Field(min_length=1, max_length=20000)
    description: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def entry_placeholder(self):
        if '{case}' not in self.command:
            raise ValueError('运行模板必须包含 {case} 占位符')
        return self


class CoverageConfig(BaseModel):
    """Declarative coverage integration; execution is not enabled by this schema."""
    model_config = ConfigDict(extra='forbid')
    tool: str = Field(min_length=1, max_length=100, description='覆盖率工具，例如 vcs-urg')
    description: str = Field(min_length=1, max_length=2000, description='采集环境、版本、源码同步边界和必要前置条件')
    artifact_path: str = Field(min_length=1, max_length=4000, description='本次运行产物路径，必须包含 {run_dir}')
    extract_command: str = Field(min_length=1, max_length=20000, description='提取或校验本次运行覆盖率的命令')
    merge_command: str = Field(min_length=1, max_length=20000, description='按 {inputs_manifest} 中的本批次数据库合并，输出到 {merge_dir}')
    report_path: str = Field(min_length=1, max_length=4000, description='合并后报告路径，必须包含 {merge_dir}')
    parser: Literal['line-coverage-json-v1'] = 'line-coverage-json-v1'
    build_id_command: str = Field(min_length=1, max_length=20000, description='输出实际构建标识的命令，覆盖 RTL、参数和覆盖率编译配置')

    @model_validator(mode='after')
    def paths_and_merge(self):
        if '{run_dir}' not in self.artifact_path:
            raise ValueError('artifact_path 必须包含 {run_dir}，确保每次运行独立保存')
        if '{merge_dir}' not in self.report_path:
            raise ValueError('report_path 必须包含 {merge_dir}，不得覆盖单次运行产物')
        if not all(token in self.merge_command for token in ('{inputs_manifest}', '{merge_dir}')):
            raise ValueError('merge_command 必须使用 {inputs_manifest} 和 {merge_dir}')
        return self


class CoverageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    coverage: CoverageConfig | None


class CasesDefinition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    runner: RunTemplate | None = None
    coverage: CoverageConfig | None = None
    targets: list[TestCase] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique_ids(self):
        ids = [n.id for n in self.targets]
        bindings = [b.id for n in self.targets for b in n.bindings]
        if len(ids) != len(set(ids)) or len(bindings) != len(set(bindings)):
            raise ValueError('用例或绑定 ID 重复')
        return self


class Cases(Targets):
    definition_model = CasesDefinition
    target_model = TestCase
    prefixes = {'case': 'tc'}

    def __init__(self, root):
        super().__init__(root)
        self.path = self.root / '.flowcanvas/testcases.json'

    def reserve(self, name, values, revision, owner):
        with LOCK:
            data = self.read()
            row = TestCase(id=new_id('tc'), name=name, **values,
                           lifecycle='creating', operation_status='running', operation_session=owner)
            data.targets.append(row)
            saved = self.save(data, revision)
            return {**row.model_dump(), 'revision': saved.revision}

    def lifecycle(self, target_id, operation, revision, owner):
        with LOCK:
            data = self.read()
            row = next((n for n in data.targets if n.id == target_id), None)
            if row is None: raise ValueError('测试用例不存在')
            if operation == 'finish_create':
                if row.lifecycle != 'creating': raise ValueError('该用例不在创建中')
                if not any(b.role == 'definition' for b in row.bindings) or not row.description.strip() or self.status()['reports'][target_id]['status'] != 'bound':
                    raise ValueError('完成创建前必须填写说明并完成有效源码绑定')
                row.lifecycle = 'ready'; row.operation_status = 'paused'; row.operation_session = ''
            elif operation == 'begin_delete':
                for path in (self.root / '.flowcanvas/test-runs').glob('*.json'):
                    run = json.loads(path.read_text())
                    if run.get('case_id') == target_id and run.get('status') in {'starting', 'running'}:
                        raise ValueError('该用例仍在运行，请先停止运行')
                row.lifecycle = 'deleting'; row.operation_status = 'running'; row.operation_session = owner
            elif operation == 'resume_operation':
                if row.lifecycle == 'ready': raise ValueError('没有待恢复操作')
                row.operation_status = 'running'; row.operation_session = owner
            saved = self.save(data, revision)
            return {**row.model_dump(), 'revision': saved.revision}

    def reconcile_operations(self, sessions):
        from .subagents import execution_active
        with LOCK:
            data = self.read(); changed = False
            for row in data.targets:
                if row.lifecycle == 'ready' or row.operation_status != 'running': continue
                owner = sessions.get(row.operation_session)
                if not owner or not execution_active(owner):
                    row.operation_status = 'paused'; changed = True
            if changed: self.save(data, data.revision)

    def delete(self, target_id, expected_revision):
        """Remove one case definition; keep source edits explicit and preserve run history."""
        with LOCK:
            data = self.read()
            case = next((row for row in data.targets if row.id == target_id), None)
            if not case:
                raise ValueError('测试用例不存在')
            if case.lifecycle != 'deleting':
                raise ValueError('请先 begin_delete 登记删除操作')
            for path in (self.root / '.flowcanvas/test-runs').glob('*.json'):
                run = json.loads(path.read_text())
                if run.get('case_id') == target_id and run.get('status') in {'starting', 'running'}:
                    raise ValueError('该用例仍在运行，请先停止运行')
            data.targets = [row for row in data.targets if row.id != target_id]
            saved = self.save(data, expected_revision)
            return {'deleted': target_id, 'revision': saved.revision}

    def discover(self, files):
        """Scan declarations, resolving UVM inheritance across the selected sources."""
        if not files:
            ignored = {'.git', '.flowcanvas', '.venv', 'venv', 'node_modules', '__pycache__', 'build', 'dist'}
            files = [str(p.relative_to(self.root)) for p in self.root.rglob('*')
                     if p.suffix in {'.sv', '.svh', '.py'} and not ignored.intersection(p.relative_to(self.root).parts)
                     and p.is_file() and p.resolve().is_relative_to(self.root)]
        if len(files) > 2000:
            raise ValueError('扫描超过 2000 个源码文件，请指定文件列表')
        parsed, errors, classes, candidates = {}, {}, {}, []
        for file in sorted(set(files)):
            try:
                result = inventory(self.file(file)); parsed[file] = result
                if file.endswith('.py'):
                    tree = ast.parse(result['text'])
                    # pytest default discovery; nested helpers and fixture methods aren't tests.
                    if not (Path(file).name.startswith('test_') or Path(file).name.endswith('_test.py')):
                        continue
                    names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_')}
                    methods = {(c.name, n.name) for c in tree.body if isinstance(c, ast.ClassDef) and c.name.startswith('Test')
                               and not any(isinstance(n, ast.FunctionDef) and n.name == '__init__' for n in c.body)
                               for n in c.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_')}
                    for row in result['nodes']:
                        if row['kind'] in {'FunctionDef', 'AsyncFunctionDef'} and ((not row['scope'] and row['name'] in names) or (row['scope'], row['name']) in methods):
                            candidates.append(dict(row, file=file, framework='pytest'))
                else:
                    for row in result['nodes']:
                        if row['kind'] == 'ClassDeclaration':
                            classes.setdefault(row['name'], []).append(dict(row, file=file, framework='uvm'))
            except (ValueError, SyntaxError, OSError) as exc:
                errors[file] = str(exc)
        def is_test(name, seen):
            if name in {'uvm_test', 'uvm_pkg::uvm_test'}: return True
            if name in seen: return False
            rows = classes.get(name, [])
            # Ambiguous names are never guessed across packages.
            return len(rows) == 1 and is_test(rows[0].get('base', ''), seen | {name})
        for name, rows in classes.items():
            if is_test(name, set()): candidates.extend(rows)
        return {'cases': candidates, 'errors': errors, 'files': sorted(parsed), 'scanned_files': len(set(files))}

    def import_files(self, files, expected_revision, selected):
        with LOCK:
            data = self.read()
            if data.revision != expected_revision: raise ValueError('定义已更新，请重新读取')
            if not data.runner: raise ValueError('导入前请先用 configure 设置项目全局运行模板')
            scan = self.discover(files)
            if scan['errors']: raise ValueError('请先修复扫描错误或缩小文件列表：' + str(scan['errors']))
            if not isinstance(selected, list) or not selected:
                raise ValueError('请先检查候选源码，仅在 values.selected 中指定可独立运行的用例：file、start、fingerprint、reason、title、description')
            chosen = []
            for item in selected:
                if not isinstance(item, dict) or not str(item.get('reason', '')).strip():
                    raise ValueError('每个选中用例需要 reason，说明具体激励与运行入口；不能仅凭继承关系导入')
                if not isinstance(item.get('title'), str) or not item['title'].strip():
                    raise ValueError('每个导入用例必须提供 title，概括测试场景，不能只填写代码类名或函数名')
                if not isinstance(item.get('description'), str) or not item['description'].strip():
                    raise ValueError('每个导入用例必须提供 AI 根据源码生成的 description，说明验证目的、主要激励与检查行为')
                matches = [row for row in scan['cases'] if all(row.get(key)==item.get(key) for key in ('file','start','fingerprint'))]
                if len(matches) != 1:
                    raise ValueError('选中用例不在候选列表或源码已变化，请重新 discover')
                if not any(row['file']==matches[0]['file'] and row['start']==matches[0]['start'] for row in chosen):
                    chosen.append(dict(matches[0], title=item['title'].strip(), description=item['description'].strip()))
            original, written, additions = {}, {}, []
            known = {b.id for n in data.targets for b in n.bindings}
            grouped = {}
            for row in chosen: grouped.setdefault(row['file'], []).append(row)
            try:
                counts = Counter()
                for file in scan['files']: counts.update(inventory(self.file(file))['markers'])
                if any(n > 1 for n in counts.values()): raise ValueError('存在重复源码标识，请先修复')
                for file, rows in grouped.items():
                    path = self.file(file); parsed = inventory(path); original[path] = parsed['text']
                    if parsed['orphans']: raise ValueError(file + ' 有失联标识，请先核对')
                    text = original[path]
                    for row in sorted(rows, key=lambda n: n['start'], reverse=True):
                        if row['id'] in known: continue
                        # Revalidate the preview immediately before annotating.
                        current = next((n for n in parsed['nodes'] if n['start'] == row['start'] and n['fingerprint'] == row['fingerprint']), None)
                        if not current: raise ValueError('源码已变化，请重新扫描')
                        bid = row['id'] or new_id('bind')
                        if not row['id']:
                            pos = row['start']; indent = re_indent(text, pos)
                            prefix = '' if not text[text.rfind('\n', 0, pos)+1:pos].strip() else '\n'+indent
                            text = text[:pos]+prefix+('#' if path.suffix=='.py' else '//')+' @flowcanvas id='+bid+'\n'+indent+text[pos:]
                        name = (row['scope']+'.' if row['scope'] else '')+row['name']
                        target = TestCase(id=new_id('tc'), name=name, title=row['title'], description=row['description'], origin='ai', bindings=[Binding(id=bid,file=file,kind=row['kind'])])
                        data.targets.append(target); additions.append(target); known.add(bid)
                    if path.read_text() != original[path]: raise ValueError('源码已变化，请重新扫描')
                    if text != original[path]:
                        path.write_text(text); written[path] = text
                for target in additions:
                    binding = target.bindings[0]
                    matches = [n for n in inventory(self.file(binding.file))['nodes'] if n['id']==binding.id]
                    if len(matches)!=1: raise ValueError('源码标识无法唯一定位')
                    binding.fingerprint = matches[0]['fingerprint']
                if additions: self.save(data, expected_revision)
            except Exception:
                for path, text in written.items():
                    if path.read_text() == text: path.write_text(original[path])
                raise
            return self.status()


def execute(root, request, owner=""):
    r = Request.model_validate(request) if isinstance(request, dict) else request
    store = Cases(root)
    if r.operation == 'guide':
        return {'instructions': '新建必须先 create 注册占位用例，再编写代码，用同一 target_id update/bind，最后 finish_create 转为正式用例；不可用 import 重复登记新建用例。删除前必须 begin_delete 标记操作中，再修改专属源码，最后 delete。中断后用 resume_operation 恢复原条目，禁止重新 create。以上操作均传最新 expected_revision。先 read 获取 revision；导入前必须 configure，通过 values={command,description} 配置项目统一运行模板。command 为 bash 命令，使用 {case} 表示用例入口、{project} 表示项目根目录，入口会自动 shell 转义，不要再给占位符加引号；description 说明运行位置、依赖和源码同步边界，不写入凭据。依据项目已有脚本配置，禁止虚构运行环境。覆盖率另用 configure_coverage，values={coverage:配置对象或null}；先核对真实产物、工具和构建标识，按 schema 填写，无法验证时留 null，不编造命令。运行结束会按配置自动提取、校验和合并，必须用实际短用例验证接通后再声称成功。配置中 run_dir 是单次运行目录，merge_dir 是批次合并输出目录，inputs_manifest 是本批次输入数据库清单；构建版本不兼容时拒绝合并并提示开始新批次。configure 后重新 read 获取 revision；discover 只返回候选，继承 uvm_test 不代表应该导入。阅读候选源码及启动配置，检查具体激励、sequence 和运行入口，只导入可独立运行且承担具体测试行为的用例。排除 base_test、抽象类和只搭建环境的基类，不要仅按名称判断。import 必须通过 values.selected 显式选择，每项包含 discover 返回的 file、start、fingerprint，以及 reason（具体激励和运行入口的依据）、title（简洁中文标题，概括测试场景，不能只复述函数名或类名）、description（必须根据源码生成的中文用例说明，概述验证目的、主要激励及实际检查行为，不能只复述类名或声称已经通过）；files 包含解析继承所需的基类文件，但不选中基类。无法确认时先报告，不导入。Python 按默认 pytest 文件/函数约定，一个函数一条记录，参数化不展开。import 插入稳定 ID 注释，重复导入幂等。create 创建待实现记录，name 填代码类名/函数名，values.title 必须填写可读标题；title 用于界面展示，name 和源码绑定保留运行标识，不可用标题替换入口。inspect 查看语法节点，再 bind 用 start/fingerprint 绑定。update 通过 values.title 修改标题，可另行填写已确认的 name 和 description。用例说明只保留 description 一个文本字段。accept 必须核对源码。不要修改 JSON 或从扫描推断执行通过。delete 需要 target_id 与最新 expected_revision，只移除该用例登记，不删除源码或历史日志；先确认运行已停止，并检查共享驱动与引用，专属源码按用户授权单独修改。运行、监控和停止使用 test_case_runs 工具：start 传 case_id，之后 read 传返回的 run_id 和 offset，直到运行结束并读完日志，不能重复 start 或把进程成功退出等同于功能验证通过。写操作需要完全访问。', 'schema': CasesDefinition.model_json_schema()}
    if r.operation == 'configure':
        with LOCK:
            data=store.read(); data.runner=RunTemplate.model_validate(r.values)
            store.save(data,r.expected_revision)
            return store.status()
    if r.operation == 'configure_coverage':
        config=CoverageRequest.model_validate(r.values)
        with LOCK:
            data=store.read(); data.coverage=config.coverage
            store.save(data,r.expected_revision)
            return store.status()
    if r.operation == 'discover': return store.discover(r.files)
    if r.operation == 'read': return store.status()
    if r.operation == 'inspect': return store.inspect(r.files)
    if r.operation == 'import': return store.import_files(r.files,r.expected_revision,r.values.get('selected'))
    if r.operation in {'create', 'update'} and (r.operation == 'create' or 'title' in r.values):
        if not isinstance(r.values.get('title'), str) or not r.values['title'].strip():
            raise ValueError('请填写测试用例标题（title）')
        r.values['title'] = r.values['title'].strip()
    if r.operation == 'create': return store.reserve(r.name,r.values,r.expected_revision,owner)
    if r.operation in {'begin_delete', 'finish_create', 'resume_operation'}:
        return store.lifecycle(r.target_id,r.operation,r.expected_revision,owner)
    if r.operation == 'update': return store.update(r.target_id,r.values,r.expected_revision)
    if r.operation == 'delete': return store.delete(r.target_id,r.expected_revision)
    if r.operation == 'bind': return store.bind(r.target_id,r.values,r.expected_revision)
    if r.operation == 'accept': return store.accept(r.target_id,r.expected_revision,r.values)
    if r.operation == 'source': return store.source(r.binding_id)
    raise ValueError('未知操作')


def routes(projects, sessions=None):
    router = APIRouter(prefix='/api/chat/projects')
    def store(identity):
        project = next((p for p in projects.state['recent'] if p['id']==identity), None)
        if not project: raise HTTPException(404,'项目不存在')
        result = Cases(project['path'])
        if sessions is not None: result.reconcile_operations(sessions)
        return result
    @router.get('/{identity}/testcases/revision')
    def revision(identity: str):
        return {'revision': store(identity).read().revision}
    @router.get('/{identity}/testcases')
    def read(identity: str):
        try: return store(identity).status()
        except (ValueError,OSError) as exc: raise HTTPException(422,str(exc)) from None
    @router.post('/{identity}/testcases')
    def action(identity: str, request: Request):
        try: return execute(store(identity).root,request)
        except (ValueError,OSError,KeyError,StopIteration,SyntaxError) as exc: raise HTTPException(422,str(exc)) from None
    return router
