"""Durable scoped conversation ownership and validated per-message viewing context."""
import json
from pathlib import Path
from types import SimpleNamespace
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .testcases import Cases


class CaseView(BaseModel):
    """Bounded browser selection; authoritative case and artifact metadata come from the server."""
    model_config = ConfigDict(extra='forbid')
    tab: Literal['overview','source','coverage','runs','wave'] = 'overview'
    module_instance: str | None = Field(default=None,max_length=4000)
    coverage_metric: Literal['line','toggle','branch','expr','cond','fsm','assert'] | None = None
    coverage_file: str | None = Field(default=None,max_length=4096)
    binding_id: str | None = Field(default=None,max_length=100)
    run_id: str | None = Field(default=None,pattern='^[0-9a-f]{32}$')
    waveform: str | None = Field(default=None,max_length=2000)
    line: int | None = Field(default=None,ge=1)
    end_line: int | None = Field(default=None,ge=1)


class CaseBackend(BaseModel):
    """Choose a backend and optionally materialize an idempotent new scoped draft."""
    backend: Literal['codex','opencode'] = 'codex'
    draft_id: str = Field(default='',pattern='^([0-9a-f]{32})?$')


def case_record(service, project_id, case_id):
    """Resolve an opened project and live case ID without changing the active project."""
    if case_id.startswith('module_'):
        from .module_details import module_record
        project,card,_=module_record(service,project_id,identity=case_id)
        return project,card
    project=next((p for p in service.projects.state['recent'] if p['id']==project_id),None)
    if not project:raise HTTPException(404,'Project not found')
    if case_id.startswith('coverage_'):
        metric=case_id.removeprefix('coverage_')
        if metric not in {'line','toggle','branch','expr','cond','fsm','assert'}:
            raise HTTPException(404,'Coverage metric not found')
        return project,SimpleNamespace(id=case_id,title=metric+' coverage',name=metric,description='Project coverage metric '+metric,entity_type='coverage')
    if case_id.startswith('bug_'):
        from .bugs import Bugs
        bug=next((row for row in Bugs(project['path']).read().targets if row.id==case_id),None)
        if bug is None:raise HTTPException(404,'Bug not found')
        return project,SimpleNamespace(id=bug.id,title=bug.name,name=bug.name,description=bug.description,
                                       entity_type='bug',bindings=bug.bindings,
                                       details={key:getattr(bug,key) for key in ('status','severity','condition','expected','actual','evidence','case_ids','example')})
    if case_id.startswith(('vt_','fg_','fp_','tp_')):
        from .targets import Targets
        target=next((row for row in Targets(project['path']).read().targets if row.id==case_id),None)
        if target is None:raise HTTPException(404,'Verification target not found')
        return project,SimpleNamespace(id=target.id,title=target.name,name=target.name,description=target.description,
                                       entity_type='target',kind=target.kind,parent=target.parent,bindings=target.bindings,explanation=target.explanation)
    card=next((c for c in Cases(project['path']).read().targets if c.id==case_id),None)
    if not card:raise HTTPException(404,'Test case not found')
    return project,card


def view_snapshot(service, session, view):
    """Freeze server-verified case/source/run context before a turn is queued or launched."""
    binding=session.get('case_binding')
    if not binding:raise HTTPException(409,'Viewing context requires a case-bound conversation')
    project,card=case_record(service,binding['project_id'],binding['case_id'])
    if Path(session['project_root']).resolve()!=Path(project['path']).resolve():
        raise HTTPException(409,'Conversation belongs to another project')
    value=dict(project_id=project['id'],project_root=project['path'],case_id=card.id,
               title=card.title,name=card.name,description=card.description,view=view.model_dump(exclude_none=True))
    if getattr(card,'entity_type','')=='coverage':
        from .module_details import module_record
        from .coverage_sources import source_snapshot
        if view.tab not in {'source','coverage'} or view.coverage_metric!=card.name or not view.module_instance or view.binding_id or view.run_id or view.waveform:
            raise HTTPException(422,'Coverage context must match the conversation metric and selected module')
        _,selected,_=module_record(service,project['id'],instance=view.module_instance)
        source=source_snapshot(project['path'],view.module_instance,card.name,view.coverage_file)
        lines=source['content'].splitlines()
        if view.line and view.line>len(lines) or view.end_line and (not view.line or view.end_line<view.line or view.end_line>len(lines)):
            raise HTTPException(422,'Invalid coverage source selection range')
        start=view.line or source['line'];end=view.end_line or (min(len(lines),start+120) if view.line else source['end_line'])
        source['content']='\n'.join(lines[start-1:end])[:12000];source['selection_line']=start;source['selection_end_line']=end
        value.update(entity_type='coverage',metric=card.name,instance=selected.instance,module=selected.name,source=source)
        return value
    if getattr(card,'entity_type','')=='module':
        from .module_details import module_record
        if view.tab not in {'overview','source','coverage'} or view.binding_id or view.run_id or view.waveform:
            raise HTTPException(422,'Design conversations support overview, source and coverage context')
        _,selected,source=module_record(service,project['id'],identity=card.id,instance=view.module_instance or '')
        source.pop('file_content',None);source['content']=source['content'][:12000]
        value.update(entity_type='design',instance=selected.instance,module=selected.name,source=source,children=selected.children)
        if view.tab=='overview':value['overview']=selected.overview
        if view.tab=='coverage':
            from .coverage_runtime import public
            state=public(project['path'])
            value['current_source']=value.pop('source')
            value['coverage']=dict(metric=view.coverage_metric or 'line',file=view.coverage_file,build_id=state.get('build_id'),report_path='.flowcanvas/coverage')
        return value
    target_view=getattr(card,'entity_type','')=='target'
    if target_view:
        from .targets import Targets
        if view.tab not in {'overview','source'} or view.run_id or view.waveform or view.module_instance:
            raise HTTPException(422,'Target conversations support overview and bound source only')
        store=Targets(project['path']);status=store.status()
        value.update(entity_type='target',kind=card.kind,parent=card.parent,explanation=card.explanation.model_dump() if card.explanation else None,
                     explanation_status=status['reports'][card.id].get('explanation_status','missing'),
                     bindings=status['reports'][card.id]['bindings'],
                     coverage=status.get('coverage',{}).get('targets',{}).get(card.id))
    bug_view=getattr(card,'entity_type','')=='bug'
    if bug_view:
        from .bugs import Bugs
        if view.tab not in {'overview','source'} or view.run_id or view.waveform or view.module_instance:
            raise HTTPException(422,'Bug conversations support details and bound source only')
        store=Bugs(project['path'])
        value.update(entity_type='bug',bug=card.details)
    if view.tab=='coverage':raise HTTPException(422,'Coverage view belongs to the design tab')
    if view.binding_id:
        source=next((b for b in card.bindings if b.id==view.binding_id),None)
        if not source:raise HTTPException(422,'Source binding does not belong to this case')
        value['source']=(store if target_view or bug_view else Cases(project['path'])).source(source.id)
        # Full source can be read through normal tools; freeze only the bound selection here.
        full=value['source'].pop('file_content',None)
        if full is not None and view.line and view.line>len(full.splitlines()):
            raise HTTPException(422,'Source view line exceeds file length')
        if view.end_line and (not view.line or view.end_line<view.line):
            raise HTTPException(422,'Invalid source selection range')
        if len(value['source'].get('content',''))>12000:
            value['source']['content']=value['source']['content'][:12000]
            value['source']['truncated']=True
    if view.run_id:
        metadata=Path(project['path'])/'.flowcanvas/test-runs'/(view.run_id+'.json')
        if not metadata.is_file():raise HTTPException(404,'Run not found')
        run=json.loads(metadata.read_text())
        if run.get('case_id')!=card.id:raise HTTPException(422,'Run does not belong to this case')
        value['run']={key:run.get(key) for key in ('id','status','mode','command','started','ended','exit_code','build_id','source_fingerprint','run_dir','coverage_status')}
        if view.waveform:
            directory=(metadata.parent/view.run_id).resolve();path=(directory/view.waveform).resolve()
            if not path.is_relative_to(directory) or not path.is_file() or path.suffix.lower() not in {'.vcd','.fst','.fsdb','.vpd','.wlf'}:
                raise HTTPException(422,'Waveform does not belong to this run')
    elif view.waveform:raise HTTPException(422,'A waveform requires its run ID')
    return value


def routes(service):
    """Look up scoped histories; materialize drafts once on first send, serialized by ASGI."""
    router=APIRouter(prefix='/api/chat/projects')
    def lookup(project_id,case_id,draft_id=''):
        """Resolve a specific draft or the most recently updated non-archived scoped history."""
        binding=dict(project_id=project_id,case_id=case_id)
        candidates=[s for s in service.sessions.values() if s.get('case_binding')==binding
                    and (s.get('case_draft_id')==draft_id if draft_id else not s.get('archived'))]
        return max(candidates,key=lambda s:s.get('updated',0),default=None)
    @router.get('/{project_id}/coverage/{case_id}/conversation')
    @router.get('/{project_id}/bugs/{case_id}/conversation')
    @router.get('/{project_id}/targets/{case_id}/conversation')
    @router.get('/{project_id}/modules/{case_id}/conversation')
    @router.get('/{project_id}/testcases/{case_id}/conversation')
    async def inspect(project_id:str,case_id:str):
        """Opening a case must never create an empty conversation or launch a backend."""
        case_record(service,project_id,case_id)
        return dict(session=lookup(project_id,case_id))
    @router.post('/{project_id}/coverage/{case_id}/conversation')
    @router.post('/{project_id}/bugs/{case_id}/conversation')
    @router.post('/{project_id}/targets/{case_id}/conversation')
    @router.post('/{project_id}/modules/{case_id}/conversation')
    @router.post('/{project_id}/testcases/{case_id}/conversation')
    async def materialize(project_id:str,case_id:str,value:CaseBackend):
        """Reuse default history or create exactly one session for an explicit new draft."""
        project,card=case_record(service,project_id,case_id)
        session=lookup(project_id,case_id,value.draft_id)
        if session is None:
            session=service.create(value.backend,project=project,case_binding=dict(project_id=project_id,case_id=case_id))
            if value.draft_id:
                session['case_draft_id']=value.draft_id
                service.save(session)
        return session
    return router
