"""Project bug records; demonstration records never count as confirmed defects."""
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import Field, model_validator
from .testcases import TestCase, CasesDefinition
from .targets import Targets, Request as TargetRequest, LOCK, new_id


class Bug(TestCase):
    id: str = Field(pattern=r'^bug_[0-9a-f]{32}$')
    kind: Literal['bug'] = 'bug'
    status: Literal['unverified','confirmed','fixing','resolved'] = 'unverified'
    severity: Literal['low','medium','high','critical'] = 'medium'
    actual: str = ''
    evidence: str = ''
    case_ids: list[str] = Field(default_factory=list)
    example: bool = False

    @model_validator(mode='after')
    def evidence_required(self):
        if self.example and self.status != 'unverified':
            raise ValueError('示例只能标为未验证；真实发现请建立独立记录')
        if self.status != 'unverified' and not self.evidence.strip():
            raise ValueError('确认或修复 Bug 必须提供复现或回归证据')
        return self


class Definition(CasesDefinition):
    targets: list[Bug] = Field(default_factory=list)


class Request(TargetRequest):
    kind: Literal['bug'] = 'bug'


class Bugs(Targets):
    definition_model = Definition
    target_model = Bug
    prefixes = {'bug':'bug'}

    def __init__(self,root):
        super().__init__(root)
        self.path=self.root/'.flowcanvas/bugs.json'

    def write_bug(self, request):
        with LOCK:
            data=self.read()
            values=request.values
            allowed={'name','description','condition','expected','actual','evidence','case_ids','status','severity','example'}
            if set(values)-allowed:raise ValueError('包含不支持的 Bug 字段')
            if request.operation=='create':
                bug=Bug.model_validate(dict(values,id=new_id('bug'),name=request.name,origin='ai'))
                data.targets.append(bug)
            else:
                old=next((n for n in data.targets if n.id==request.target_id),None)
                if not old:raise ValueError('Bug 不存在')
                if 'example' in values and values['example']!=old.example:raise ValueError('不能将示例直接转为真实 Bug')
                bug=Bug.model_validate({**old.model_dump(),**values});data.targets[data.targets.index(old)]=bug
            if bug.case_ids:
                from .testcases import Cases
                known={n.id for n in Cases(self.root).read().targets}
                if set(bug.case_ids)-known:raise ValueError('关联测试用例不存在')
            self.save(data,request.expected_revision)
            return bug.model_dump()


def execute(root,request):
    r=Request.model_validate(request) if isinstance(request,dict) else request
    store=Bugs(root)
    if r.operation=='guide':return {'instructions':'先 read 获取 revision。create/update 填写名称、description、condition（复现步骤）、expected、actual、severity、status、evidence 和 case_ids。未知内容留空，不能把猜测当作确认缺陷。初始状态 unverified；confirmed/fixing/resolved 必须提供证据，resolved 需要修复及回归依据。example=true 仅用于用户要求的演示，保持 unverified，不得转为真实缺陷。inspect/bind 可关联已确认的源码位置，source 查看完整源码；不能给假设 Bug 捏造定位。写操作需要完全访问。','schema':Definition.model_json_schema()}
    if r.operation=='read':return store.status()
    if r.operation in {'create','update'}:return store.write_bug(r)
    if r.operation=='inspect':return store.inspect(r.files)
    if r.operation=='bind':return store.bind(r.target_id,r.values,r.expected_revision)
    if r.operation=='source':return store.source(r.binding_id)
    if r.operation=='accept':return store.accept(r.target_id,r.expected_revision,r.values)
    raise ValueError('未知操作')


def routes(projects):
    router=APIRouter(prefix='/api/chat/projects')
    def root(identity):
        project=next((p for p in projects.state['recent'] if p['id']==identity),None)
        if not project:raise HTTPException(404,'项目不存在')
        return project['path']
    @router.get('/{identity}/bugs/revision')
    def revision(identity:str):return {'revision':Bugs(root(identity)).read().revision}
    @router.get('/{identity}/bugs')
    def read(identity:str):
        try:return Bugs(root(identity)).status()
        except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from None
    @router.post('/{identity}/bugs')
    def action(identity:str,request:Request):
        try:return execute(root(identity),request)
        except (ValueError,OSError,KeyError,StopIteration,SyntaxError) as exc:raise HTTPException(422,str(exc)) from None
    return router
