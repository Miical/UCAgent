"""Durable one-case/one-conversation ownership and validated per-message viewing context."""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .testcases import Cases


class CaseView(BaseModel):
    """Bounded browser selection; authoritative case and artifact metadata come from the server."""
    model_config = ConfigDict(extra='forbid')
    tab: Literal['overview','source','runs','wave'] = 'overview'
    binding_id: str | None = Field(default=None,max_length=100)
    run_id: str | None = Field(default=None,pattern='^[0-9a-f]{32}$')
    waveform: str | None = Field(default=None,max_length=2000)
    line: int | None = Field(default=None,ge=1)
    end_line: int | None = Field(default=None,ge=1)


class CaseBackend(BaseModel):
    """Choose the backend only when materializing a case's first conversation."""
    backend: Literal['codex','opencode'] = 'codex'


def case_record(service, project_id, case_id):
    """Resolve an opened project and live case ID without changing the active project."""
    project=next((p for p in service.projects.state['recent'] if p['id']==project_id),None)
    if not project:raise HTTPException(404,'Project not found')
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
    if view.binding_id:
        source=next((b for b in card.bindings if b.id==view.binding_id),None)
        if not source:raise HTTPException(422,'Source binding does not belong to this case')
        value['source']=Cases(project['path']).source(source.id)
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
    """Look up without creating; materialize once on the first user send, serialized by ASGI."""
    router=APIRouter(prefix='/api/chat/projects')
    def lookup(project_id,case_id):
        """Session metadata is the durable binding index, including archived conversations."""
        binding=dict(project_id=project_id,case_id=case_id)
        return next((s for s in service.sessions.values() if s.get('case_binding')==binding),None)
    @router.get('/{project_id}/testcases/{case_id}/conversation')
    async def inspect(project_id:str,case_id:str):
        """Opening a case must never create an empty conversation or launch a backend."""
        case_record(service,project_id,case_id)
        return dict(session=lookup(project_id,case_id))
    @router.post('/{project_id}/testcases/{case_id}/conversation')
    async def materialize(project_id:str,case_id:str,value:CaseBackend):
        """Reuse the sole binding even for duplicate requests from separate browser tabs."""
        project,card=case_record(service,project_id,case_id)
        session=lookup(project_id,case_id)
        if session is None:
            session=service.create(value.backend,project=project,case_binding=dict(project_id=project_id,case_id=case_id))
        return session
    return router
