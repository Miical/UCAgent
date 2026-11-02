"""Persistent chat sessions and SSE projections, independent of backend protocols."""
import asyncio
import copy
import json
from pathlib import Path
import time
import uuid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal
from .backends.codex import CodexBackend

ACTIVE = {'starting', 'running', 'stopping'}


class Message(BaseModel):
    """A bounded user message and explicit catalog selections."""
    text: str = Field(min_length=1, max_length=64000)
    quote: str = Field(default='', max_length=64000)
    model: str = Field(min_length=1, max_length=200)
    effort: str = Field(min_length=1, max_length=40)
    speed: Literal["standard", "fast", "ultrafast"] = "standard"


PermissionMode = Literal['full', 'workflow', 'readonly']


class PermissionSettings(BaseModel):
    """User-selected execution policy for subsequent turns in a conversation."""
    mode: PermissionMode


class SessionSettings(BaseModel):
    """Editable conversation metadata, independent of native model history."""
    title: str = Field(min_length=1, max_length=120)
    archived: bool = False
    pinned: bool = False


class ChatService:
    """Serialize session transitions on the ASGI event loop and persist completed turns."""

    def __init__(self, directory):
        """Load only FlowCanvas sessions; native histories remain owned by Codex."""
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace = self.root / 'workspace'
        workspace.mkdir(exist_ok=True)
        self.sessions = {}
        self.jobs = set()
        self.launching = set()
        self.steering = set()
        self.dirty = set()
        self.catalog = []
        self.backend = CodexBackend(workspace, self.event)
        self.flusher = None
        for path in self.root.glob('*.json'):
            session = json.loads(path.read_text())
            if session['status'] in ACTIVE:
                session.update(status='interrupted', error='Server restarted during the previous turn.')
            session['queue_paused'] = True  # Restart never silently executes persisted requests.
            self.sessions[session['id']] = session

    def save(self, session):
        """Atomically persist the UI projection without rewriting native model history."""
        if self.sessions.get(session['id']) is not session:
            return
        if session['status'] == 'completed':
            for item in reversed(session['items']):
                if item['kind'] == 'user' and not item.get('steered'):
                    break
                if item['kind'] == 'text':
                    item['turn_completion'] = {'started_at': session.get('started_at'),
                                               'ended_at': session.get('ended_at')}
                    break
        target = self.root / (session['id'] + '.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(session, ensure_ascii=False))
        temporary.chmod(0o600)
        temporary.replace(target)
        self.dirty.discard(session['id'])

    async def flush(self):
        """Bound streaming disk writes while retaining partial replies across restarts."""
        while True:
            await asyncio.sleep(1)
            for identity in list(self.dirty):
                self.save(self.sessions[identity])
            for session in list(self.sessions.values()):
                if (session.get('queue') and not session.get('queue_paused')
                        and session['status'] not in ACTIVE and session['id'] not in self.launching
                        and session['id'] not in self.steering):
                    queued = session['queue'].pop(0)
                    self.launch(session, Message(**queued['message']), queued['tier'])

    def changed(self, session):
        """Advance the public revision and schedule a durable checkpoint."""
        if self.sessions.get(session['id']) is not session:
            return
        session['revision'] += 1
        session['updated'] = time.time()
        self.dirty.add(session['id'])

    def get(self, identity):
        """Resolve opaque local identities without accepting arbitrary paths."""
        if identity not in self.sessions:
            raise HTTPException(404, 'Conversation not found')
        return self.sessions[identity]

    def create(self):
        """Create a lazy local session; no model call or native thread yet."""
        session = {'id': uuid.uuid4().hex, 'backend': 'codex', 'title': '', 'thread': None,
                   'turn': None, 'status': 'idle', 'items': [], 'model': '', 'effort': '',
                   'revision': 0, 'updated': time.time(), 'error': '', 'usage': None,
                   'permission_mode': 'workflow'}
        self.sessions[session['id']] = session
        self.save(session)
        return session

    def event(self, event):
        """Apply backend events only to their owning conversation and turn."""
        if event['kind'] == 'disconnected':
            for session in self.sessions.values():
                if session['status'] in ACTIVE:
                    session.update(status='error', error=event['error'], ended_at=time.time())
                    self.changed(session)
            return
        session = next((s for s in self.sessions.values() if s['thread'] == event.get('thread')), None)
        if session is None:
            return
        kind = event['kind']
        if kind == 'turn':
            session['turn'] = event['turn']
            if not (session['status'] == 'stopping' and event['status'] == 'inProgress'):
                session['status'] = {'inProgress': 'running', 'completed': 'completed',
                                     'interrupted': 'interrupted', 'failed': 'error'}.get(event['status'], 'running')
            session['error'] = event['error']
            if session['status'] == 'error':
                session['queue_paused'] = True
            if session['status'] not in ACTIVE:
                session['ended_at'] = time.time()
                for block in session['items']:
                    if block.get('status') in {'running', 'inProgress'}:
                        block.update(status=session['status'], ended_at=time.time())
            if session['status'] in {'interrupted', 'error'}:
                for item in session['items']:
                    if item.get('status') in {'running', 'inProgress'}:
                        item['status'] = session['status']
        elif kind in ('item', 'delta'):
            identity = event['item']['id'] if kind == 'item' else event['id']
            item = next((i for i in session['items'] if i['id'] == identity), None)
            if item is None:
                item = {'id': identity, 'kind': event.get('type', 'text'), 'text': '', 'status': 'running',
                        'title': '', 'turn': event.get('turn'), 'started_at': time.time()}
                session['items'].append(item)
            if kind == 'item':
                item.update(event['item'])
                if item['status'] not in {'running', 'inProgress'}:
                    item['ended_at'] = time.time()
            else:
                # Summary fragments are independently indexed by the native protocol.
                if event['type'] == 'reasoning':
                    parts = item.setdefault('parts', {})
                    key = str(event['part'])
                    parts[key] = parts.get(key, '') + event['text']
                    item['text'] = '\n\n'.join(parts[k] for k in sorted(parts, key=int))
                else:
                    item['text'] += event['text']
        elif kind == 'usage':
            session['usage'] = event['usage']
        elif kind == 'error':
            session['error'] = event['error']
            if not event['retrying']:
                session['status'] = 'error'
        self.changed(session)
        if kind == 'turn' and session['status'] not in ACTIVE:
            self.save(session)

    def launch(self, session, message, tier):
        """Start one accepted request after its predecessor has fully settled."""
        self.save(session)
        session.update(status='starting', error='', turn=None, model=message.model, effort=message.effort,
                       started_at=time.time(), ended_at=None, speed=message.speed, service_tier=tier)
        if not session['title']:
            session['title'] = message.text.strip()[:40]
        session['items'].append({'id': uuid.uuid4().hex, 'kind': 'user', 'text': message.text,
                                 'quote': message.quote, 'status': 'completed', 'started_at': session['started_at']})
        self.launching.add(session['id'])
        self.changed(session)
        self.save(session)
        job = asyncio.create_task(self.run(session, message))
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)

    async def run(self, session, message):
        """Open the native thread and start generation, respecting early cancellation."""
        try:
            mode = session.get('permission_mode', 'workflow')
            session['thread'] = await self.backend.open(session['thread'], message.model, mode)
            self.changed(session)
            if session['status'] == 'stopping':
                session['status'] = 'interrupted'
                return
            turn = await self.backend.send(session['thread'], ('Quoted context (reference only):\n' + message.quote + '\n\nUser request:\n' if message.quote else '') + message.text, message.model, message.effort,
                                           session.get('service_tier'), mode)
            session['turn'] = turn
            # Notifications may precede the RPC response: never overwrite terminal state.
            if session['status'] == 'stopping':
                await self.backend.interrupt(session['thread'], turn)
        except Exception as exc:
            session.update(status='error', error=str(exc), queue_paused=True)
        finally:
            self.launching.discard(session['id'])
            if session['status'] not in ACTIVE:
                session['ended_at'] = session.get('ended_at') or time.time()
            self.changed(session)
            self.save(session)

    async def close(self):
        """Stop owned jobs and checkpoint partial conversation data on shutdown."""
        for job in self.jobs:
            job.cancel()
        if self.jobs:
            await asyncio.gather(*self.jobs, return_exceptions=True)
        await self.backend.close()
        if self.flusher:
            self.flusher.cancel()
        for session in self.sessions.values():
            if session['status'] in ACTIVE:
                session['status'] = 'interrupted'
            self.save(session)


def routes(service):
    """Expose a backend-neutral local chat API and reconnectable event stream."""
    router = APIRouter(prefix='/api/chat')

    @router.get('/backends')
    async def backends():
        """Discover implemented backends and live model capabilities."""
        try:
            service.catalog = await service.backend.models()
            return [{'id': 'codex', 'name': 'Codex', 'models': service.catalog}]
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.get('/sessions')
    async def sessions():
        """List lightweight session summaries without loading all messages."""
        return [{**{k: s[k] for k in ('id', 'title', 'status', 'updated', 'backend')},
                 'archived': s.get('archived', False), 'pinned': s.get('pinned', False)}
                for s in sorted(service.sessions.values(), key=lambda s: s['updated'], reverse=True)]

    @router.post('/sessions')
    async def create():
        """Create a new independently addressable conversation."""
        return service.create()

    @router.get('/sessions/{identity}')
    async def get(identity: str):
        """Read persisted UI state without resuming or invoking the model."""
        return service.get(identity)

    @router.post('/sessions/{identity}/delete')
    async def delete(identity: str):
        """Permanently delete FlowCanvas history, leaving native Codex storage untouched."""
        session = service.get(identity)
        if session['status'] in ACTIVE:
            raise HTTPException(409, 'Stop the active turn before deleting')
        (service.root / (identity + '.json')).unlink(missing_ok=True)
        service.dirty.discard(identity)
        del service.sessions[identity]
        return {'deleted': identity}

    @router.post('/sessions/{identity}/permissions')
    async def permissions(identity: str, settings: PermissionSettings):
        """Change policy only between turns; clients cannot mutate an executing turn."""
        session = service.get(identity)
        if session['status'] in ACTIVE:
            raise HTTPException(409, 'Stop the active turn before changing permissions')
        session['permission_mode'] = settings.mode
        service.changed(session)
        service.save(session)
        return session

    @router.post('/sessions/{identity}/settings')
    async def settings(identity: str, settings: SessionSettings):
        """Persist labels and reversible organization without deleting conversation data."""
        session = service.get(identity)
        if not settings.title.strip():
            raise HTTPException(400, 'Conversation title cannot be blank')
        if settings.archived and session['status'] in ACTIVE:
            raise HTTPException(409, 'Stop the active turn before archiving')
        session.update(title=settings.title.strip(), archived=settings.archived, pinned=settings.pinned)
        service.changed(session)
        service.save(session)
        return session

    @router.post('/sessions/{identity}/messages', status_code=202)
    async def send(identity: str, message: Message):
        """Accept one turn atomically; reject duplicates while a turn is active."""
        session = service.get(identity)
        model = next((m for m in service.catalog if m['id'] == message.model), None)
        if not model or message.effort not in model['efforts']:
            raise HTTPException(400, 'Select a model and reasoning effort from the current catalog')
        if message.speed != 'standard' and message.speed not in model.get('speed_tiers', {}):
            raise HTTPException(400, 'Selected speed tier is not available for this model')
        if not message.text.strip():
            raise HTTPException(400, 'Message is empty')
        tier = model.get('speed_tiers', {}).get(message.speed)
        if session['status'] in ACTIVE or session.get('queue') or identity in service.launching:
            queue = session.setdefault('queue', [])
            if len(queue) >= 20:
                raise HTTPException(409, 'Message queue is full')
            queue.append({'id': uuid.uuid4().hex, 'message': message.model_dump(), 'tier': tier})
            service.changed(session)
            service.save(session)
        else:
            session['queue_paused'] = False
            service.launch(session, message, tier)
        return session

    @router.post('/sessions/{identity}/queue/{queued_id}/{action}')
    async def queue_action(identity: str, queued_id: str, action: str):
        """Remove a queued request or prioritize it and interrupt the current turn."""
        session = service.get(identity)
        queue = session.setdefault('queue', [])
        queued = next((item for item in queue if item['id'] == queued_id), None)
        if queued is None:
            raise HTTPException(404, 'Queued message not found')
        if identity in service.steering:
            raise HTTPException(409, 'Guidance is being delivered')
        if action not in {'remove', 'send', 'steer'}:
            raise HTTPException(400, 'Unknown queue action')
        if action == 'steer':
            if session['status'] != 'running' or not session.get('turn'):
                raise HTTPException(409, 'No active turn to guide; retry after the turn starts or finishes')
            message = Message(**queued['message'])
            text = ('Quoted context (reference only):\n' + message.quote + '\n\nUser request:\n' if message.quote else '') + message.text
            service.steering.add(identity)
            try:
                await service.backend.steer(session['thread'], session['turn'], text)
            except Exception as exc:
                raise HTTPException(409, str(exc)) from exc
            finally:
                service.steering.discard(identity)
            session['items'].append({'id': uuid.uuid4().hex, 'kind': 'user', 'text': message.text,
                                     'quote': message.quote, 'steered': True, 'status': 'completed',
                                     'started_at': time.time()})
        queue.remove(queued)
        if action == 'send':
            queue.insert(0, queued)
            # Pause draining until the interrupt request has been accepted.
            session['queue_paused'] = True
            if session['status'] in ACTIVE:
                await stop(identity)
            session['queue_paused'] = False
        service.changed(session)
        service.save(session)
        return session

    @router.post('/sessions/{identity}/stop')
    async def stop(identity: str):
        """Cancel the current native turn, including requests made during startup."""
        session = service.get(identity)
        session['queue_paused'] = True
        service.changed(session)
        if session['status'] not in ACTIVE:
            return session
        session['status'] = 'stopping'
        service.changed(session)
        if session['turn']:
            try:
                await service.backend.interrupt(session['thread'], session['turn'])
            except Exception as exc:
                session.update(status='error', error=str(exc))
                service.changed(session)
                raise HTTPException(502, str(exc)) from exc
        return session

    @router.get('/sessions/{identity}/events')
    async def events(identity: str, request: Request):
        """Stream coalesced revisions; reconnection always begins with a full snapshot."""
        session = service.get(identity)

        async def stream():
            """Coalesce token bursts without queuing unbounded data for slow browsers."""
            revision, heartbeat = -1, 0
            while identity in service.sessions and not await request.is_disconnected():
                if session['revision'] != revision:
                    revision = session['revision']
                    yield 'data: ' + json.dumps(copy.deepcopy(session), ensure_ascii=False) + '\n\n'
                heartbeat += 1
                if heartbeat % 150 == 0:
                    yield ': heartbeat\n\n'
                await asyncio.sleep(.1)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={
            'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    return router
