"""Explicit test discovery and stable source bindings, independent of run results."""
import json
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .targets import Targets, Binding, LOCK




class RunTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command: str = Field(min_length=1, max_length=20000)
    description: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def entry_placeholder(self):
        if '{case}' not in self.command:
            raise ValueError('运行模板必须包含 {case} 占位符')
        import shlex
        try:
            words=shlex.split(self.command)
        except ValueError as exc:
            raise ValueError("运行模板的引号不匹配") from exc
        for word in words:
            if "{case}" in word and word.partition("{case}")[0].endswith(("/", "\\")):
                raise ValueError("{case} 已包含完整入口路径，不要添加目录前缀")

        return self


class TestCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^tc_[0-9a-f]{32}$')
    kind: Literal['case'] = 'case'
    parent: None = None
    name: str = Field(min_length=1, max_length=200, description='代码中的测试类名或函数名，不作为可读标题')
    title: str = Field(default='', max_length=200, description='用例的可读标题；新建和导入必须填写，概括测试场景')
    description: str = ''
    runner: RunTemplate | None = None
    lifecycle: Literal['draft', 'ready', 'creating', 'updating', 'deleting'] = 'ready'
    operation_status: Literal['running', 'paused'] = 'paused'
    operation_session: str = ''
    origin: Literal['manual', 'ai', 'scan'] = 'manual'
    bindings: list[Binding] = Field(default_factory=list)


class CoverageConfig(BaseModel):
    """Declarative coverage integration; execution is not enabled by this schema."""
    model_config = ConfigDict(extra='forbid')
    provider: Literal['commands', 'vcs'] = 'commands'
    mode: Literal['shared', 'per_run'] = 'shared'
    tool: str = Field(min_length=1, max_length=100, description='覆盖率工具，例如 vcs-urg')
    description: str = Field(min_length=1, max_length=2000, description='采集环境、版本、源码同步边界和必要前置条件')
    artifact_path: str = Field(default='{run_dir}/coverage.json', min_length=1, max_length=4000, description='本次运行产物路径，必须包含 {run_dir}')
    extract_command: str = Field(default='', min_length=0, max_length=20000, description='Read the shared database (shared mode) or this run (per_run mode) and write artifact_path')
    merge_command: str = Field(default='', max_length=20000, description='Only per_run mode: merge {inputs_manifest} into {merge_dir}')
    report_path: str = Field(default='', max_length=4000, description='Only per_run mode: merged report path containing {merge_dir}')
    parser: Literal['coverage-json-v1', 'line-coverage-json-v1'] = 'coverage-json-v1'
    build_id_command: str = Field(default='', min_length=0, max_length=20000, description='输出实际构建标识的命令，覆盖 RTL、参数和覆盖率编译配置')

    @model_validator(mode='before')
    @classmethod
    def legacy_mode(cls, value):
        """Preserve explicitly configured independent-run merge integrations."""
        if isinstance(value, dict) and 'mode' not in value and value.get('merge_command'):
            value = dict(value, mode='per_run')
        return value

    @model_validator(mode='after')
    def paths_and_merge(self):
        if self.provider=='vcs':
            if self.mode!='shared' or any((self.extract_command,self.build_id_command,self.merge_command,self.report_path)):
                raise ValueError('Native VCS uses fixed metadata and platform extraction, not project commands')
            return self
        if not self.extract_command or not self.build_id_command:
            raise ValueError('Command coverage requires extract_command and build_id_command')
        if '{run_dir}' not in self.artifact_path:
            raise ValueError('artifact_path 必须包含 {run_dir}，确保每次运行独立保存')
        if self.mode == 'shared':
            if '{coverage_db}' not in self.extract_command:
                raise ValueError('Shared extraction must read {coverage_db}')
            if self.merge_command or self.report_path:
                raise ValueError('Shared coverage publishes artifact_path directly; do not merge cumulative snapshots')
            return self
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

    instructions = """Use inspect/apply/begin_modify/update/end_modify in batches. inspect reads cards and raw files.
apply(count) allocates draft IDs. begin_modify(ids,action=update|delete) registers this session's work.
update(cards=[{id,name,title,description,runner,bindings}]) sets the code entry, readable Chinese title,
source-grounded description and complete bindings. Python name is the pytest entry without the
file prefix (test_name or TestClass::test_name); UVM name is the actual runnable test class.
Each binding is {file,line,line_count,role}; existing markers use id instead of line.
Use role=definition for the actual case implementation. Line numbers are one-based; the server
inserts stable marker comments and verifies their ranges, not language syntax. Do not invent entries.
end_modify(ids) requires a valid definition binding, title, description and entry; planned completion
is unavailable for cases. Resume with the original IDs. Running cases cannot be edited or deleted.
Import by reading existing source, choosing real runnable cases and using this same lifecycle;
do not register environment-only base classes. Prefer existing documentation for concise Chinese
text, otherwise derive it from actual stimuli and checks. Never infer passing results from bindings.
Each case runner is {command,description} and is set via update. Shared coverage settings use
test_case_runs(operation=configure,settings={coverage}); inspect returns the existing settings. Each runner command requires standalone {case}, expanded and
shell-quoted once by the server; Python entries include the absolute file path. Reports use {run_dir}.
Coverage settings declare real extract/merge/build identity commands with isolated run artifacts;
do not fabricate results. Test execution uses test_case_runs start/read/stop/list.
"""

    def check_idle(self, ids):
        """Reject card mutations while a selected test is executing."""
        for path in (self.root/'.flowcanvas/test-runs').glob('*.json'):
            run=json.loads(path.read_text())
            if run.get('case_id') in ids and run.get('status') in {'starting','running'}:
                raise ValueError('Stop the running case before editing or deleting it')

    def validate_card(self, card, require_runner=True):
        """Validate source-backed metadata and, when required, the executable test entry."""
        import re
        if require_runner and card.runner is None:
            raise ValueError('Configure this case runner before finishing')
        if not card.title.strip() or not re.fullmatch(r'[A-Za-z_]\w*(?:(?:::|\.)[A-Za-z_]\w*)*',card.name):
            raise ValueError('Provide a readable title and valid test entry name')
        if not any(b.role=='definition' for b in card.bindings):
            raise ValueError('A case requires a definition source binding')


def execute(root, request, owner=""):
    """Use the same five-operation engine as verification target cards."""
    from .target_cards import execute as cards_execute
    result=cards_execute(root,request,owner,store=Cases(root))
    operation=request.get('operation','inspect') if isinstance(request,dict) else request.operation
    if operation=='inspect':
        result['configuration_schema']=CasesDefinition.model_json_schema()
    return result


def configure(root, settings, expected_revision):
    """Update project execution settings separately from card mutations."""
    if set(settings)-{'coverage'}:
        raise ValueError('Project settings accept coverage only; set runner on each case with update')
    with LOCK:
        store=Cases(root)
        if any(json.loads(p.read_text()).get('status') in {'starting','running'}
               for p in (store.root/'.flowcanvas/test-runs').glob('*.json')):
            raise ValueError('Wait until the project run ends before configuring execution')
        data=store.read()
        if 'coverage' in settings:
            data.coverage=CoverageConfig.model_validate(settings['coverage']) if settings['coverage'] is not None else None
        store.save(data,expected_revision)
        return store.status()


def routes(projects, sessions=None):
    from .target_cards import CardRequest
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
    def action(identity: str, request: CardRequest):
        try: return execute(store(identity).root,request)
        except (ValueError,OSError,KeyError,StopIteration,SyntaxError) as exc: raise HTTPException(422,str(exc)) from None
    return router
