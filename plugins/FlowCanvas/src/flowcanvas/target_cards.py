"""Atomic, session-owned batch operations for verification target cards."""
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .targets import Binding, Definition, LOCK, SKILL, Target, Targets, new_id, re_indent
from .line_bindings import MARKER, fingerprint, span


class CardRequest(BaseModel):
    """Five public operations; card patches and selection are always explicit."""
    model_config = ConfigDict(extra='forbid')
    operation: Literal['inspect', 'apply', 'begin_modify', 'update', 'end_modify'] = 'inspect'
    expected_revision: int = Field(default=0, ge=0)
    count: int = Field(default=0, ge=0, le=500)
    ids: list[str] = Field(default_factory=list, max_length=500)
    cards: list[dict] = Field(default_factory=list, max_length=500)
    action: Literal['update', 'delete'] = 'update'
    cascade: bool = False
    planned: bool = False
    kind: Literal['group', 'point', 'testpoint'] | None = None
    parent: str | None = None
    files: list[str] = Field(default_factory=list, max_length=100)
    binding_id: str = ''


def execute(root, request, owner='manual'):
    """Validate a complete batch before publishing source markers and one revision."""
    r = request if isinstance(request, CardRequest) else CardRequest.model_validate(request)
    owner = owner or 'manual'
    store = Targets(root)
    with LOCK:
        data = store.read()
        if r.operation == 'inspect':
            result = store.status()
            if r.ids:
                unknown = set(r.ids) - {t.id for t in data.targets}
                if unknown:
                    raise ValueError('Unknown card IDs: ' + ', '.join(sorted(unknown)))
            selected = [t for t in result['targets'] if (not r.ids or t['id'] in r.ids)
                        and (r.kind is None or t['kind'] == r.kind)
                        and (r.parent is None or t['parent'] == r.parent)]
            result['targets'] = selected
            result['reports'] = {t['id']: result['reports'][t['id']] for t in selected}
            result['summary'] = dict(Counter(v['status'] for v in result['reports'].values()))
            if r.files:
                result['sources'] = store.inspect(r.files)
            if r.binding_id:
                result['source'] = store.source(r.binding_id)
            result['instructions'] = SKILL.read_text()
            result['request_schema'] = CardRequest.model_json_schema()
            return result
        if data.revision != r.expected_revision:
            raise ValueError('Revision changed; inspect and retry with the current revision')
        if r.operation == 'apply':
            if not r.count or r.cards or r.ids:
                raise ValueError('apply requires only count and expected_revision')
            created = [new_id('vt') for _ in range(r.count)]
            data.targets.extend(Target(id=identity, name='待完善', lifecycle='draft',
                                       operation_session=owner,
                                       origin='ai' if owner != 'manual' else 'manual')
                                for identity in created)
            saved = store.save(data, r.expected_revision)
            return {'revision': saved.revision, 'ids': created}

        ids = [item.get('id') for item in r.cards] if r.operation == 'update' else r.ids
        if not ids or len(set(ids)) != len(ids) or any(not isinstance(i, str) for i in ids):
            raise ValueError('Provide a nonempty batch of unique card IDs')
        by_id = {t.id: t for t in data.targets}
        unknown = set(ids) - by_id.keys()
        if unknown:
            raise ValueError('Unknown card IDs: ' + ', '.join(sorted(unknown)))
        selected = set(ids)
        if r.operation == 'begin_modify' and r.action == 'delete':
            descendants = set(selected)
            while True:
                children = {t.id for t in data.targets if t.parent in descendants}
                if children <= descendants:
                    break
                descendants.update(children)
            if descendants != selected and not r.cascade:
                raise ValueError('Deletion includes children; provide all child IDs or cascade=true')
            selected = descendants
        rows = [t for t in data.targets if t.id in selected]
        if r.operation == 'begin_modify':
            for row in rows:
                if row.operation_session and row.operation_session != owner:
                    raise ValueError('Card is owned by another session: ' + row.id)
                if row.lifecycle not in {'ready', 'draft', 'creating', 'updating', 'deleting'}:
                    raise ValueError('Invalid card state: ' + row.id)
                if row.lifecycle in {'creating', 'updating', 'deleting'}:
                    previous = 'delete' if row.lifecycle == 'deleting' else 'update'
                    if previous != r.action:
                        raise ValueError('Cannot change an active operation action: ' + row.id)
                parent = by_id.get(row.parent)
                if parent and parent.lifecycle == 'deleting' and r.action != 'delete':
                    raise ValueError('Parent is being deleted: ' + row.id)
            for row in rows:
                row.lifecycle = 'deleting' if r.action == 'delete' else ('creating' if row.lifecycle in {'draft', 'creating'} else 'updating')
                row.operation_session = owner
                row.operation_status = 'running'
        else:
            for row in rows:
                if (row.operation_session != owner or row.operation_status != 'running'
                        or row.lifecycle not in {'creating', 'updating', 'deleting'}):
                    raise ValueError('Call begin_modify for this session before writing: ' + row.id)
            if r.operation == 'update':
                if any(t.lifecycle == 'deleting' for t in rows):
                    raise ValueError('Cannot update a card registered for deletion')
                return update_cards(store, data, r)
            deleting = {t.id for t in rows if t.lifecycle == 'deleting'}
            if deleting and len(deleting) != len(rows):
                raise ValueError('Finish updates and deletions in separate batches')
            if deleting:
                if any(t.parent in deleting and t.id not in deleting for t in data.targets):
                    raise ValueError('Include all registered children in the deletion batch')
                data.targets = [t for t in data.targets if t.id not in deleting]
            else:
                reports = store.status()['reports']
                for row in rows:
                    if row.kind is None or not row.description.strip() or row.name == '待完善':
                        raise ValueError('Provide a readable title and description: ' + row.id)
                    if row.bindings:
                        if reports[row.id]['status'] != 'bound':
                            raise ValueError('Resolve source bindings before finishing: ' + row.id)
                    elif not r.planned:
                        raise ValueError('Binding required; use planned=true only for unimplemented targets: ' + row.id)
                    row.lifecycle = 'ready'
                    row.operation_status = 'paused'
                    row.operation_session = ''
        saved = store.save(data, r.expected_revision)
        return {'revision': saved.revision, 'ids': sorted(selected),
                'deleted': sorted(selected) if r.operation == 'end_modify' and deleting else [],
                'cards': [t.model_dump() for t in saved.targets if t.id in selected]}


def update_cards(store, data, request):
    """Commit explicit line-range bindings and marker insertions as one batch."""
    by_id = {t.id: t for t in data.targets}
    original, inserts, pending = {}, {}, []
    for patch in request.cards:
        if set(patch) - {'id', 'kind', 'name', 'description', 'parent', 'bindings'}:
            raise ValueError('Unsupported card information field')
        row = by_id[patch['id']]
        if 'kind' in patch and row.kind is not None and row.kind != patch['kind']:
            raise ValueError('An assigned card kind cannot change')
        for key in ('kind','name','description','parent'):
            if key in patch:
                setattr(row,key,patch[key])
        if 'bindings' not in patch:
            continue
        row.bindings=[]
        for spec in patch['bindings']:
            if not isinstance(spec,dict) or not {'file','line_count'} <= spec.keys():
                raise ValueError('Each binding requires file and line_count')
            if set(spec)-{'id','file','line','line_count','role','fingerprint'}:
                raise ValueError('Bindings accept id/file/line/line_count/role/fingerprint')
            path=store.file(spec['file'])
            text=original.setdefault(path,path.read_text(encoding='utf-8'))
            bid=spec.get('id')
            if bid:
                matches=[i+1 for i,line in enumerate(text.splitlines())
                         if (m := MARKER.fullmatch(line)) and m[1]==bid]
                if len(matches)!=1:
                    raise ValueError('Binding marker must exist exactly once: '+bid)
                start=matches[0]+1
            else:
                start=spec.get('line')
            end, content=span(text,start,spec['line_count'])
            if spec.get('fingerprint') and spec['fingerprint']!=fingerprint(content):
                raise ValueError('Source range changed; inspect and select it again')
            if not bid:
                bid=new_id('bind')
                if start in inserts.setdefault(path,{}):
                    raise ValueError('Use distinct marker positions for separate bindings')
                indent=re_indent(text, sum(len(x) for x in text.splitlines(keepends=True)[:start-1]))
                inserts[path][start]=indent+('#' if path.suffix=='.py' else '//')+' @flowcanvas id='+bid+'\n'
            binding=Binding(id=bid,file=str(path.relative_to(store.root)),kind='LineRange',
                            role=spec.get('role','definition'),line_count=spec['line_count'],
                            fingerprint=fingerprint(content))
            row.bindings.append(binding)
            pending.append(binding)
    Definition.model_validate(data.model_dump())
    for patch in request.cards:
        row=by_id[patch['id']]
        if row.parent and by_id[row.parent].lifecycle=='deleting':
            raise ValueError('Parent is being deleted')
    written={}
    try:
        for path,text in original.items():
            if path.read_text(encoding='utf-8')!=text:
                raise ValueError('Source changed while preparing the batch')
            lines=text.splitlines(keepends=True)
            for line,marker in sorted(inserts.get(path,{}).items(),reverse=True):
                lines.insert(line-1,marker)
            updated=''.join(lines)
            if updated!=text:
                path.write_text(updated,encoding='utf-8');written[path]=updated
        for binding in pending:
            text=store.file(binding.file).read_text(encoding='utf-8')
            positions=[i+1 for i,line in enumerate(text.splitlines())
                       if (m := MARKER.fullmatch(line)) and m[1]==binding.id]
            if len(positions)!=1:
                raise ValueError('Binding marker must be unique')
            _,content=span(text,positions[0]+1,binding.line_count)
            binding.fingerprint=fingerprint(content)
        saved=store.save(data,request.expected_revision)
    except Exception:
        for path,text in written.items():
            if path.read_text(encoding='utf-8')==text:
                path.write_text(original[path],encoding='utf-8')
        raise
    return {'revision':saved.revision,'ids':[p['id'] for p in request.cards]}
