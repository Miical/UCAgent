"""Validated source-backed module explanations shared by AI tools and the design UI."""
import hashlib
import json
from pathlib import Path
import threading
from pydantic import BaseModel, ConfigDict, Field
from .module_details import module_record
from .targets import atomic

_LOCK=threading.RLock()
_GUIDE=Path(__file__).parent/'guides/module-overview-guide.md'


class SourceRef(BaseModel):
    """A current project source range supporting an explanation."""
    model_config=ConfigDict(extra='forbid')
    file:str=''
    line:int=Field(ge=1)
    end_line:int|None=Field(default=None,ge=1)


class Assignment(SourceRef):
    """An exact source excerpt and the behavior it implements."""
    expression:str=Field(min_length=1,max_length=6000)
    meaning:str=Field(min_length=1,max_length=2000)


class Explanation(SourceRef):
    """One flat explanation with optional concrete implementation excerpts."""
    title:str=Field(default='',max_length=160)
    text:str=Field(min_length=1,max_length=4000)
    confidence:str=Field(default='source',pattern='^(source|inferred)$')
    assignments:list[Assignment]=Field(default_factory=list,max_length=16)


class Component(Explanation):
    """A functional unit in the conceptual structure, not necessarily an RTL instance."""
    id:str=Field(min_length=1,max_length=80)
    title:str=Field(min_length=1,max_length=80)
    detail:str=Field(default='',max_length=120)
    kind:str=Field(default='block',pattern='^(block|storage|interface)$')


class Connection(BaseModel):
    """A directed data or control relation between declared functional units."""
    model_config=ConfigDict(extra='forbid',populate_by_name=True)
    origin:str=Field(alias='from')
    to:str
    label:str=Field(max_length=80)
    kind:str=Field(default='relation',pattern='^(relation|data|control|status)$')


class Structure(BaseModel):
    """A small semantic graph; the application supplies layout and drawing."""
    model_config=ConfigDict(extra='forbid')
    nodes:list[Component]=Field(min_length=1,max_length=24)
    edges:list[Connection]=Field(default_factory=list,max_length=64)


class ModuleOverview(BaseModel):
    """Stable module overview contract, independent of language or verification framework."""
    model_config=ConfigDict(extra='forbid')
    purpose:Explanation
    structure:Structure
    submodules:dict[str,str]=Field(default_factory=dict)
    mechanisms:list[Explanation]=Field(min_length=1,max_length=16)
    rules:list[Explanation]=Field(min_length=1,max_length=16)
    ports:dict[str,str]=Field(default_factory=dict)
    parameters:list[Explanation]=Field(default_factory=list,max_length=32)


def execute(service,session,operation,instance,overview,expected_revision,source_revision):
    """Inspect or atomically save one parsed module with source and write-conflict guards."""
    binding=session.get('case_binding') or {}
    if binding.get('case_id')!='module_design':raise ValueError('Open the design conversation first')
    if not instance:raise ValueError('Specify the selected module instance')
    project,card,source=module_record(service,binding['project_id'],instance=instance)
    root=Path(project['path']).resolve();file=(root/'.flowcanvas/module-overviews.json').resolve()
    if not file.is_relative_to(root):raise ValueError('Overview storage escapes project')
    with _LOCK:
        raw=file.read_bytes() if file.is_file() else b'{}'
        if len(raw)>1024*1024:raise ValueError('Overview storage exceeds 1 MB')
        revision=hashlib.sha256(raw).hexdigest()
        source_bytes=(root/source['file']).read_bytes();fingerprint=hashlib.sha256(source_bytes).hexdigest()
        if operation=='inspect':
            return dict(instance=card.instance,module=card.name,source=source,overview=card.overview,children=card.children,
                        expected_revision=revision,source_revision=fingerprint,schema=ModuleOverview.model_json_schema(),
                        guide_path=str(_GUIDE),guide=_GUIDE.read_text(encoding='utf-8'),
                        instructions='Read the returned guide first and follow its content and presentation requirements. Read actual RTL and related sources. Write concise Chinese purpose, functional structure, mechanisms, rules, ports and parameters. Describe every direct child from children in submodules, keyed by its relative key: explain its role in the parent, not just its type name. Do not invent children or flatten grandchildren. No timeline. Never force a fixed node count or reuse a four-block template. Preserve actual branches, fanout, feedback and parallel relationships. Use node kind block/storage/interface and edge kind data/control/status/relation. Graph nodes represent actual storage/control/datapath roles; edges describe actual relationships, not execution steps. Supply node IDs, titles, short diagram details, explanatory text, confidence, assignments and source ranges; layout is automatic. Every structure node needs one short sentence of text naming its responsibility or relationship. Keep structure captions compact; put conditions, timing, boundary rules and assignments in mechanisms or rules instead. Use assignments with exact source excerpts and their meaning. Mark unsupported conclusions inferred in confidence and prose. Save with current expected_revision and source_revision. Never edit UI code or managed JSON, run simulations, or claim verification passed.')
        if operation!='save':raise ValueError('operation must be inspect or save')
        if session.get('permission_mode') not in {'workspace','full'}:raise ValueError('Workspace access is required')
        if revision!=expected_revision or source_revision!=fingerprint:raise ValueError('Source or overview changed; inspect again before saving')
        value=ModuleOverview.model_validate(overview).model_dump(by_alias=True,exclude_none=True)
        if set(value['submodules'])!={child['key'] for child in card.children}:
            raise ValueError('Submodule descriptions must cover exactly the current direct child keys from inspect')
        if any(not text.strip() or len(text)>600 for text in value['submodules'].values()):
            raise ValueError('Each submodule needs a concise nonempty description of at most 600 characters')
        identifiers=[node['id'] for node in value['structure']['nodes']]
        if len(set(identifiers))!=len(identifiers):raise ValueError('Duplicate structure node IDs')
        if any(edge['from'] not in identifiers or edge['to'] not in identifiers for edge in value['structure']['edges']):raise ValueError('Unknown structure edge endpoint')
        if set(value['ports'])-{port['name'] for port in card.ports}:raise ValueError('Unknown module port')
        references=[value['purpose'],*value['structure']['nodes'],*value['mechanisms'],*value['rules'],*value['parameters']]
        references.extend(assignment for item in [value['purpose'],*value['structure']['nodes'],*value['mechanisms'],*value['rules'],*value['parameters']] for assignment in item['assignments'])
        for ref in references:
            path=(root/(ref.get('file') or source['file'])).resolve()
            if not path.is_relative_to(root) or not path.is_file():raise ValueError('Source reference escapes project or does not exist')
            if path.stat().st_size>2*1024*1024:raise ValueError('Referenced source exceeds 2 MB')
            lines=path.read_text(encoding='utf-8').splitlines();start=ref['line'];end=ref.get('end_line',start)
            if end<start or end>len(lines):raise ValueError('Source reference range is invalid')
            if 'expression' in ref and ''.join(ref['expression'].split()) not in ''.join('\n'.join(lines[start-1:end]).split()):raise ValueError('Implementation excerpt does not match cited source')
            ref['file']=str(path.relative_to(root))
        value['source_sha256']=fingerprint
        entries=json.loads(raw);entries[source['file']+'::'+card.name]=value
        encoded=json.dumps(entries,ensure_ascii=False,indent=2)+'\n'
        if len(encoded.encode())>1024*1024:raise ValueError('Overview storage exceeds 1 MB')
        if (root/source['file']).read_bytes()!=source_bytes:raise ValueError('Source changed while saving; inspect again')
        atomic(file,entries)
        return dict(saved=True,instance=instance,expected_revision=hashlib.sha256(file.read_bytes()).hexdigest(),overview=value)
