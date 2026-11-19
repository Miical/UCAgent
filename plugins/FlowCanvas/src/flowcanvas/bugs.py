"""Project bug records; demonstration records never count as confirmed defects."""
from collections import Counter
import fcntl
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .testcases import Cases, TestCase, CasesDefinition
from .target_cards import CardRequest, execute as execute_cards
from .line_bindings import fingerprint, inspect_files, span
from .target_explanations import SourceAnchor, SourceReference, read_locations
from .targets import Targets, Request as TargetRequest, LOCK, new_id


class BugSourceReference(SourceReference):
    """A source citation with server-assigned identity for the Bug source tab."""
    id: str = Field(default_factory=lambda: new_id('bind'), pattern=r'^bind_[0-9a-f]{32}$')
    anchor: SourceAnchor
    role: Literal['definition','stimulus','check','coverage','rtl','configuration'] = 'rtl'


BugReviewStatus = Literal['unverified', 'as_designed', 'confirmed']


class Bug(TestCase):
    id: str = Field(pattern=r'^bug_[0-9a-f]{32}$')
    kind: Literal['bug'] = 'bug'
    status: BugReviewStatus = 'unverified'
    review_note: str = ''
    review_expected: str = ''
    severity: Literal['low','medium','high','critical'] = 'medium'
    condition: str = ''
    impact: str = ''
    static_category: str = ''
    source_refs: list[BugSourceReference] = Field(default_factory=list, max_length=24)
    expected: str = ''
    actual: str = ''
    evidence: str = ''
    case_ids: list[str] = Field(default_factory=list)
    example: bool = False

    @field_validator('status', mode='before')
    @classmethod
    def normalize_legacy_status(cls, status):
        return 'confirmed' if status in ('fixing', 'resolved') else status

    @model_validator(mode='after')
    def validate_example_status(self):
        if self.example and self.status != 'unverified':
            raise ValueError('示例只能标为未验证；真实发现请建立独立记录')
        return self


class Definition(CasesDefinition):
    targets: list[Bug] = Field(default_factory=list)


class Request(TargetRequest):
    kind: Literal['bug'] = 'bug'


class BugReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    status: BugReviewStatus
    review_note: str | None = None
    review_expected: str | None = None
    evidence: str | None = None


class Bugs(Targets):
    definition_model = Definition
    target_model = Bug
    prefixes = {'bug':'bug'}

    instructions = """Manage Bug cards with inspect, apply, begin_modify, update, and end_modify.
Inspect returns the current revision, records and source; use ids, files or binding_id as needed.
Create with apply(count), begin_modify(ids), update(cards=[{id,name,description,condition,impact,bindings,case_ids}]),
then end_modify(ids). To delete, begin_modify(ids,action=delete), then end_modify(ids).
Use the latest expected_revision and resume paused work with the same IDs and conversation.
A Bug requires a title and description. Source is optional; bindings use file, line and line_count.
Bind the smallest relevant ranges. The server anchors them without editing the RTL.
New findings stay unverified. The Bug panel records the human verdict. For confirmed defects, review_expected holds the optional human-provided expected behavior. Demos remain unverified."""
    editable_fields = {'name','description','condition','impact','source_refs','expected','actual','evidence',
                       'case_ids','status','severity','example'}

    def __init__(self,root):
        super().__init__(root)
        self.path=self.root/'.flowcanvas/bugs.json'

    def save(self, definition, expected_revision):
        """Serialize the revision check and replace across the web and workflow processes."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return super().save(definition, expected_revision)

    def status(self):
        """Project citation anchors into current file locations alongside legacy bindings."""
        result = super().status()
        for bug in result['targets']:
            report = result['reports'][bug['id']]
            for row in bug['source_refs']:
                ref = BugSourceReference.model_validate(row)
                record = {'id': ref.id, 'file': ref.file, 'label': ref.label, 'role': ref.role,
                          'fingerprint': ref.anchor.fingerprint}
                try:
                    locations = read_locations(self.file(ref.file))
                    line, end_line, _ = locations.resolve(ref.anchor)
                    record.update(status='bound', line=line, end_line=end_line)
                except (OSError, ValueError) as exc:
                    record.update(status='changed', message=str(exc))
                report['bindings'].append(record)
            if report['status'] in {'planned', 'bound'} and report['bindings']:
                report['status'] = 'bound' if all(b['status']=='bound' for b in report['bindings']) else 'changed'
        result['summary'] = dict(Counter(row['status'] for row in result['reports'].values()))
        return result

    def source(self, binding_id):
        """Read current source for a validated citation or an older marked binding."""
        for bug in self.read().targets:
            for ref in bug.source_refs:
                if ref.id != binding_id:
                    continue
                locations = read_locations(self.file(ref.file))
                line, end_line, content = locations.resolve(ref.anchor)
                return {'id': ref.id, 'file': ref.file, 'status': 'bound', 'line': line, 'end_line': end_line,
                        'content': content, 'file_content': ''.join(locations.lines)}
        return super().source(binding_id)

    def anchor_reference(self, ref, locations, role='rtl'):
        """Create a source citation anchored to checked RTL without editing the file."""
        return BugSourceReference(file=ref.file, line=ref.line, end_line=ref.end_line,
                                  label=ref.label, role=role, anchor=locations.anchor(ref.line, ref.end_line or ref.line))

    def register_static_findings(self, findings, locations):
        """Add distinct pending findings in one revision and preserve existing records."""
        with LOCK:
            data = self.read()
            by_file = {}
            for bug in data.targets:
                if bug.example or not bug.source_refs:
                    continue
                relevant = bug.source_refs[:1] if bug.static_category else bug.source_refs
                for ref in relevant:
                    if ref.file not in locations:
                        continue
                    try:
                        start, end, _ = locations[ref.file].resolve(ref.anchor)
                    except ValueError:
                        continue
                    by_file.setdefault(ref.file, []).append((start, end, bug))

            created, reused = [], []
            for finding in findings:
                primary = finding.source_refs[0]
                candidates = by_file.setdefault(primary.file, [])
                end = primary.end_line or primary.line
                duplicate = next((bug for start, previous_end, bug in candidates
                                  if start <= end and primary.line <= previous_end
                                  and (bug.static_category == finding.category
                                       or (not bug.static_category and bug.name.casefold() == finding.name.casefold()))), None)
                if duplicate:
                    reused.append(duplicate.id)
                    continue
                refs = [self.anchor_reference(ref, locations[ref.file]) for ref in finding.source_refs]
                bug = Bug(id=new_id('bug'), name=finding.name, description=finding.description,
                          condition=finding.condition, impact=finding.impact, source_refs=refs,
                          static_category=finding.category, origin='scan')
                data.targets.append(bug)
                candidates.append((primary.line, end, bug))
                created.append(bug.id)
            if created:
                self.save(data, data.revision)
            return {'created': created, 'reused': list(dict.fromkeys(reused))}

    def inspect(self, files):
        """Expose numbered source lines in the same format as other card tools."""
        return inspect_files(self, files)

    def patch_bug(self, bug, values):
        """Validate Bug metadata and anchor cited ranges against live source."""
        if set(values)-self.editable_fields:
            raise ValueError('Unsupported Bug fields')
        if values.get('status', bug.status) != bug.status:
            raise ValueError('请在 Bug 详情中更新人工复核状态')
        if 'example' in values and bug.lifecycle not in {'draft','creating'} and values['example']!=bug.example:
            raise ValueError('不能将示例直接转为真实 Bug')
        values = dict(values)
        if 'source_refs' in values:
            rows = values['source_refs']
            if not isinstance(rows,list) or len(rows)>24:
                raise ValueError('Provide up to 24 source references')
            references = []
            for row in rows:
                ref = SourceReference.model_validate(row)
                path = self.file(ref.file)
                ref.file = str(path.relative_to(self.root))
                references.append(self.anchor_reference(ref, read_locations(path)))
            values['source_refs'] = references
        replacement = Bug.model_validate({**bug.model_dump(),**values})
        if replacement.case_ids:
            known = {row.id for row in Cases(self.root).read().targets}
            if set(replacement.case_ids)-known:
                raise ValueError('关联测试用例不存在')
        return replacement

    def update_card_batch(self, data, request):
        """Use the shared update operation without inserting marker comments in RTL."""
        by_id = {bug.id: bug for bug in data.targets}
        for card in request.cards:
            bug = by_id[card['id']]
            values = {key: value for key, value in card.items() if key != 'id'}
            if 'bindings' in values:
                bindings = values.pop('bindings')
                if 'source_refs' in values or not isinstance(bindings,list):
                    raise ValueError('Provide one list of card bindings')
                refs = []
                existing = {ref.id: ref for ref in bug.source_refs}
                for binding in bindings:
                    if not isinstance(binding,dict) or set(binding)-{'id','file','line','line_count','role','fingerprint'}:
                        raise ValueError('Bug bindings use file, line, line_count and optional id/role/fingerprint')
                    file, identity = binding.get('file'), binding.get('id')
                    if not isinstance(file,str) or (identity is not None and not isinstance(identity,str)):
                        raise ValueError('Provide a source file and a valid optional binding ID')
                    path = self.file(file)
                    locations = read_locations(path)
                    prior = existing.get(identity)
                    if binding.get('id') and (not prior or prior.file != str(path.relative_to(self.root))):
                        raise ValueError('Binding ID does not belong to this Bug and file')
                    line, count = binding.get('line'), binding.get('line_count')
                    if prior:
                        line, _, _ = locations.resolve(prior.anchor)
                    if type(line) is not int or type(count) is not int:
                        raise ValueError('Binding line and line_count must be positive integers')
                    end, content = span(''.join(locations.lines), line, count)
                    if binding.get('fingerprint') and binding['fingerprint'] != fingerprint(content):
                        raise ValueError('Source range changed; inspect and select it again')
                    source = SourceReference(file=str(path.relative_to(self.root)), line=line, end_line=end,
                                             label=prior.label if prior else '')
                    ref = self.anchor_reference(source, locations, binding.get('role', prior.role if prior else 'rtl'))
                    if prior:
                        ref.id = prior.id
                    refs.append(ref)
                if len(refs)>24 or len({ref.id for ref in refs}) != len(refs):
                    raise ValueError('Provide up to 24 distinct Bug source references')
            replacement = self.patch_bug(bug, values)
            if 'bindings' in card:
                replacement.bindings = []
                replacement.source_refs = refs
            data.targets[data.targets.index(bug)] = replacement
        saved = self.save(data, request.expected_revision)
        return {'revision': saved.revision, 'ids': [card['id'] for card in request.cards]}

    def review(self, identity: str, request: BugReview):
        """Save a human review without overwriting the Bug's other fields."""
        with LOCK:
            data = self.read()
            bug = next((row for row in data.targets if row.id == identity), None)
            if bug is None or bug.lifecycle != 'ready' or bug.example:
                raise ValueError('仅可复核已登记的正式 Bug')
            values = {'status': request.status}
            if request.review_note is not None:
                values['review_note'] = request.review_note.strip()
            if request.review_expected is not None:
                if request.status != 'confirmed':
                    raise ValueError('仅在确认缺陷时可填写预期行为')
                values['review_expected'] = request.review_expected.strip()
            if request.evidence is not None:
                values['evidence'] = request.evidence.strip()
            updated = Bug.model_validate({**bug.model_dump(), **values})
            data.targets[data.targets.index(bug)] = updated
            self.save(data, request.expected_revision)
            return updated.model_dump()

    def write_bug(self, request):
        """Keep existing direct record edits compatible with session-owned operations."""
        with LOCK:
            data = self.read()
            if request.operation == 'create':
                bug = Bug(id=new_id('bug'), name=request.name, origin='ai', lifecycle='creating')
            else:
                bug = next((row for row in data.targets if row.id == request.target_id), None)
                if bug is None or bug.lifecycle != 'ready':
                    raise ValueError('Bug is missing or has an operation in progress')
            replacement = self.patch_bug(bug, request.values)
            replacement.lifecycle = 'ready'
            if request.operation == 'create':
                data.targets.append(replacement)
            else:
                data.targets[data.targets.index(bug)] = replacement
            self.save(data, request.expected_revision)
            return replacement.model_dump()


def execute(root, request, owner='manual'):
    """Dispatch the shared operations and preserve legacy HTTP callers."""
    store = Bugs(root)
    operation = request.get('operation','inspect') if isinstance(request,dict) else request.operation
    if isinstance(request, CardRequest) or operation in {'inspect','apply','begin_modify','end_modify'} or (
            isinstance(request,dict) and operation == 'update' and 'cards' in request):
        result = execute_cards(root, request, owner, store)
        if operation == 'inspect':
            result['configuration_schema'] = Definition.model_json_schema()
        return result
    r = Request.model_validate(request) if isinstance(request,dict) else request
    if r.operation=='guide':return {'instructions':'先 read 获取 revision。delete 使用 target_id 和最新 expected_revision，仅删除指定 Bug 记录。create/update 填写名称、description、condition（触发条件）、impact（可能影响）、source_refs（可选，逐条填写项目相对 file、line、end_line；平台校验并保存，不改写源码）、expected、actual、severity、evidence 和 case_ids。未知内容留空，不能把猜测当作确认缺陷。新记录默认待复核，复核状态由用户在 Bug 详情中判定。example=true 仅用于用户要求的演示，保持 unverified，不得转为真实缺陷。inspect/bind 可关联已确认的源码位置，source 查看完整源码；不能给假设 Bug 捏造定位。项目内写操作支持工作区访问和完全访问。','schema':Definition.model_json_schema()}
    if r.operation=='read':return store.status()
    if r.operation in {'create','update'}:return store.write_bug(r)
    if r.operation=='source':return store.source(r.binding_id)
    with LOCK:
        if r.operation in {'delete','bind','accept'}:
            store.require_edit(r.target_id, '')
        if r.operation=='delete':return store.delete(r.target_id,r.expected_revision)
        if r.operation=='bind':return store.bind(r.target_id,r.values,r.expected_revision)
        if r.operation=='accept':return store.accept(r.target_id,r.expected_revision,r.values)
    raise ValueError('未知操作')


def routes(projects, sessions=None):
    """Expose Bug records, revisions and shared batch operations."""
    router=APIRouter(prefix='/api/chat/projects')
    def root(identity):
        project=next((p for p in projects.state['recent'] if p['id']==identity),None)
        if not project:raise HTTPException(404,'项目不存在')
        return project['path']

    def store(identity):
        """Reconcile interrupted AI mutations when the panel polls the project."""
        bugs = Bugs(root(identity))
        if sessions is not None:
            bugs.reconcile_operations(sessions)
        return bugs

    @router.get('/{identity}/bugs/revision')
    def revision(identity:str):return {'revision':store(identity).read().revision}
    @router.get('/{identity}/bugs')
    def read(identity:str):
        try:return store(identity).status()
        except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from None
    @router.post('/{identity}/bugs/{bug_id}/review')
    def review(identity: str, bug_id: str, request: BugReview):
        try:
            return store(identity).review(bug_id, request)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post('/{identity}/bugs')
    def action(identity:str,request:CardRequest | Request):
        try:return execute(root(identity),request)
        except (ValueError,OSError,KeyError,StopIteration,SyntaxError) as exc:raise HTTPException(422,str(exc)) from None
    return router
