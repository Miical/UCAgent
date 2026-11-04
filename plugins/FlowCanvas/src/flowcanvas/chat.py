"""Persistent chat sessions and SSE projections, independent of backend protocols."""
import asyncio
from contextlib import asynccontextmanager
import copy
import json
from pathlib import Path
import time
import uuid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal
from .backends import BACKENDS, NAMES
from .subagents import execution_active, refresh_activity, interrupt_children
from .workflow_tasks import WorkflowInput, attachment


BackendName = Literal['codex', 'opencode']


class NewSession(BaseModel):
    """Choose a native backend before a conversation acquires model history."""
    backend: BackendName = 'codex'


class DirectWorkflow(WorkflowInput):
    """Catalog input plus explicit model selection for a newly created conversation."""
    model: str
    effort: str
    backend: BackendName = 'codex'

ACTIVE = {'starting', 'running', 'stopping'}


class TaskCard(BaseModel):
    """Display metadata for a user-authored task, never an authorization source."""
    title: str = Field(max_length=120)
    workflow: str = Field(max_length=120)
    project: str = Field(max_length=200)
    requirements: str = Field(default="", max_length=8000)


class Message(BaseModel):
    """A bounded user message and explicit catalog selections."""
    task_card: TaskCard | None = None
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
        """Load FlowCanvas projections; each backend owns its native model history."""
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace = self.root / 'workspace'
        workspace.mkdir(exist_ok=True)
        from .projects import Projects
        self.projects = Projects(self.root)
        self.sessions = {}
        self.jobs = set()
        self.session_backends = {}
        self.workflow_bridges = {}
        self.catalog_reconnect = set()
        self.launching = set()
        self.deleting = set()
        self.steering = set()
        self.dirty = set()
        self.child_checks = {}
        self.connection_locks = {}
        self.closing = False
        self.catalogs = {}
        # Catalog readers have no owned model turn; their connection events must
        # never interrupt conversations using separate native processes.
        self.backends = {name: cls(workspace, lambda event: None) for name, cls in BACKENDS.items()}
        self.workspace = workspace
        self.flusher = None
        for path in self.root.glob('*.json'):
            session = json.loads(path.read_text())
            if session['status'] in ACTIVE:
                session.update(status='interrupted', error='Server restarted during the previous turn.')
            for card in session.get('workflow_runs', []):
                if card['status'] != 'completed':
                    card.update(status='paused', alive=False, pause_reason='工作台已重启，请恢复工作流')
            session.update(children_running=0, children_pending=False, delegation_stopped=True)
            session['queue_paused'] = True  # Restart never silently executes persisted requests.
            self.sessions[session['id']] = session
            # Import pre-card-history records, including the retired worker storage.
            # This reads historical data only; it never creates or starts a worker.
            legacy = [session.get('workflow_run')]
            for worker_path in (self.root / 'workflow_workers' / session['id']).glob('*.json'):
                try:
                    legacy.append(json.loads(worker_path.read_text()).get('workflow_run'))
                except (OSError, ValueError):
                    continue
            cards = session.setdefault('workflow_runs', [])
            existing = {card['id'] for card in cards}
            migrated = False
            for record in legacy:
                if not record or not record.get('id') or not record.get('workspace') or record['id'] in existing:
                    continue
                card = dict(record)
                card.pop('output', None)
                root = Path(record['workspace']).parent
                card.update(conversation_id=session['id'], root=str(root), alive=False,
                            workflow_id=(session.get('attachment') or {}).get('workflow_id', 'rtl-summary'),
                            stages=[], current_stage='', historical=True)
                if record.get('status') != 'completed':
                    card.update(status='paused', pause_reason='历史 MCP 服务已停止')
                try:
                    progress = json.loads((root / 'progress.json').read_text())
                    card.update(stages=progress.get('stages', []), current_stage=progress.get('current_stage', ''))
                except (OSError, ValueError):
                    pass
                cards.append(card)
                existing.add(card['id'])
                migrated = True
            from .workflow_runtime import project_startup_stage
            for card in cards:
                project_startup_stage(card)
                if 'output' in card:
                    card.pop('output')
                    migrated = True
            if isinstance(session.get('workflow_run'), dict) and 'output' in session['workflow_run']:
                session['workflow_run'].pop('output')
                migrated = True
            if migrated:
                self.save(session)

    def save(self, session):
        """Atomically persist the UI projection without rewriting native model history."""
        if self.sessions.get(session['id']) is not session:
            return
        if session.get('ended_at') and session['status'] in {'completed', 'interrupted', 'error', 'failed'}:
            if ((session.get('attachment') or {}).get('task_only') and session.get('task_attachment_started')
                    and not execution_active(session)):
                workflow_id = session['attachment']['workflow_id']
                for card in session.get('workflow_runs', []):
                    if card['workflow_id'] == workflow_id:
                        card['authorized'] = True
                session.pop('attachment', None)
                session.pop('task_attachment_started', None)
            for item in reversed(session['items']):
                if item['kind'] == 'user' and not item.get('steered'):
                    if item.get('task_card'):
                        item['task_run'] = {'status': session['status'], 'started_at': session.get('started_at'), 'ended_at': session['ended_at']}
                    break
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
                previous = self.child_checks.get(session['id'])
                if ((session.get('children_pending') or session.get('children_running'))
                        and not session.get('delegation_stopped') and (not previous or previous.done())):
                    job = asyncio.create_task(refresh_activity(self, session))
                    self.child_checks[session['id']] = job
                    self.jobs.add(job)
                    job.add_done_callback(self.jobs.discard)
            for bridge in list(self.workflow_bridges.values()):
                await bridge.runtime.inspect()
            for session in list(self.sessions.values()):
                if self.sessions.get(session['id']) is not session:
                    continue
                if session['id'] in self.catalog_reconnect and session['status'] == 'completed' and session['id'] not in self.launching and not execution_active(session):
                    bridge = self.workflow_bridges.get(session['id'])
                    card = next((c for c in session.get('workflow_runs', []) if bridge and c['id'] == bridge.active_card), None)
                    self.launching.add(session['id'])
                    continued = False
                    try:
                        await self.close_connection(session['id'])
                        # Stop can arrive while the native process is closing. Recheck
                        # its grant before scheduling a continuation of the same task.
                        if (session['id'] in self.catalog_reconnect and not session.get('delegation_stopped')
                                and card and card['status'] != 'completed'
                                and session.get('permission_mode') != 'readonly' and not session.get('queue')):
                            session.update(status='starting', turn=None, ended_at=None)
                            self.changed(session)
                            job = asyncio.create_task(self.run(session, Message(
                                text='The workflow MCP tool catalog has been refreshed in this same conversation. Continue the user-requested workflow using its now-available stage tools. Inspect list_workflow_runs for the active card and resume it only if its host is stopped. Do not restart completed work.',
                                model=session['model'], effort=session['effort'], speed=session.get('speed', 'standard'))))
                            self.jobs.add(job)
                            job.add_done_callback(self.jobs.discard)
                            continued = True
                    finally:
                        # Retain the reconnect marker and client ownership throughout
                        # the await so observers and shutdown cannot lose the handoff.
                        self.catalog_reconnect.discard(session['id'])
                        if not continued:
                            self.launching.discard(session['id'])
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

    async def close_connection(self, identity):
        """Retire a native client only after current history readers release it."""
        async with self.connection_locks.setdefault(identity, asyncio.Lock()):
            backend = self.session_backends.get(identity)
            if backend:
                emit = backend.emit
                backend.emit = lambda event: None
                try:
                    await backend.close()
                except BaseException:
                    backend.emit = emit
                    raise
                self.session_backends.pop(identity, None)

    @asynccontextmanager
    async def reconfigure(self, session, *, workflow=False, deleting=False):
        """Own idle resources until a configuration change or deletion is fully committed."""
        identity = session['id']
        if self.get(identity) is not session:
            raise HTTPException(409, 'Conversation changed')
        if session['status'] in ACTIVE or execution_active(session) or identity in self.launching:
            raise HTTPException(409, 'Wait for the current task or connection change')
        self.launching.add(identity)
        if deleting:
            self.deleting.add(identity)
            session['queue_paused'] = True
        try:
            await self.close_connection(identity)
            if workflow:
                bridge = self.workflow_bridges.get(identity)
                if bridge:
                    await bridge.close()
                    self.workflow_bridges.pop(identity, None)
            # Messages queued during closure cannot run until the caller commits
            # the new policy/attachment. Keep resources owned across every await.
            yield
        except BaseException as exc:
            session['queue_paused'] = True
            self.changed(session)
            self.save(session)
            if isinstance(exc, RuntimeError):
                raise HTTPException(502, str(exc)) from exc
            raise
        finally:
            self.launching.discard(identity)
            self.deleting.discard(identity)

    async def models(self, backend, workspace, refresh=False):
        """Cache capabilities by backend and workspace, preserving project configuration."""
        key = (backend, str(Path(workspace).resolve()))
        if refresh or key not in self.catalogs:
            self.catalogs.pop(key, None)
            try:
                self.catalogs[key] = await self.backends[backend].models(workspace=Path(key[1]), refresh=refresh)
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from exc
        return self.catalogs[key]

    def create(self, backend='codex'):
        """Create a lazy local session; no model call or native thread yet."""
        if backend not in BACKENDS:
            raise ValueError('Unknown chat backend')
        session = {'id': uuid.uuid4().hex, 'backend': backend, 'title': '', 'thread': None,
                   'turn': None, 'status': 'idle', 'items': [], 'model': '', 'effort': '',
                   'revision': 0, 'updated': time.time(), 'error': '', 'usage': None,
                   'permission_mode': 'workflow',
                   'project_root': (self.projects.current or {}).get('path'),
                   'workspace': (self.projects.current or {}).get('path', str(self.workspace))}
        self.sessions[session['id']] = session
        self.save(session)
        return session

    def event(self, event):
        """Apply backend events only to their owning conversation and turn."""
        if event['kind'] == 'disconnected':
            for session in self.sessions.values():
                if session['status'] in ACTIVE and event.get('session_id') == session['id']:
                    # Connection loss must settle partial items and pause queued work
                    # through the same durable terminal path as a failed model turn.
                    self.event({'kind': 'turn', 'session_id': session['id'],
                                'thread': session['thread'], 'turn': session['turn'],
                                'status': 'failed', 'error': event['error']})
            return
        session = next((s for s in self.sessions.values() if s['thread'] == event.get('thread')
                        and (not event.get('session_id') or s['id'] == event['session_id'])), None)
        if session is None:
            return
        kind = event['kind']
        if kind == 'turn':
            session['turn'] = event['turn']
            if session.get('stop_error') and session.get('delegation_stopped'):
                # Native completion can arrive after a failed cancellation request;
                # it cannot confirm cleanup of the other execution owners.
                session.update(status='error', error=session['stop_error'])
            elif not (session['status'] == 'stopping' and event['status'] == 'inProgress'):
                session['status'] = {'inProgress': 'running', 'completed': 'completed',
                                     'interrupted': 'interrupted', 'failed': 'error'}.get(event['status'], 'running')
                session['error'] = event['error']
            if session['status'] == 'error':
                session['queue_paused'] = True
            if session['status'] == 'completed' and not session.get('delegation_stopped'):
                session['children_pending'] = True
            if session['status'] not in ACTIVE:
                session['ended_at'] = time.time()
                for block in session['items']:
                    if block.get('status') in {'running', 'inProgress'}:
                        block.update(status=session['status'], ended_at=time.time())
            if session['status'] in {'interrupted', 'error'}:
                self.catalog_reconnect.discard(session['id'])
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
            session['error'] = session.get('stop_error') or event['error']
            if session.get('stop_error') or not event['retrying']:
                session['status'] = 'error'
        self.changed(session)
        if kind == 'turn' and session['status'] not in ACTIVE:
            self.save(session)

    def launch(self, session, message, tier):
        """Start one accepted request after its predecessor has fully settled."""
        self.save(session)
        session['delegation_stopped'] = False
        session.pop('stop_error', None)
        session.update(status='starting', error='', turn=None, model=message.model, effort=message.effort,
                       started_at=time.time(), ended_at=None, speed=message.speed, service_tier=tier)
        if not session['title']:
            session['title'] = message.text.strip()[:40]
        if (session.get('attachment') or {}).get('task_only'):
            session['task_attachment_started'] = True
        session['items'].append({'id': uuid.uuid4().hex, 'kind': 'user', 'text': message.text,
                                 'task_card': message.task_card.model_dump() if message.task_card else None, 'quote': message.quote, 'status': 'completed', 'started_at': session['started_at']})
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
            backend = self.session_backends.get(session['id'])
            if backend is None:
                backend = BACKENDS[session['backend']](Path(session.get('workspace') or self.workspace), lambda event: self.event({**event, 'session_id': session['id']}))
                from .workflow_bridge import WorkflowBridge
                bridge = self.workflow_bridges.get(session['id'])
                if bridge is None:
                    bridge = WorkflowBridge(self, session)
                    await bridge.start()
                    self.workflow_bridges[session['id']] = bridge
                backend.mcp_url = bridge.url
                self.session_backends[session['id']] = backend
            allowed = session.get('attachment')
            from .catalog import workflow_menu
            workflow_meta = next((item for item in workflow_menu() if allowed and item['id'] == allowed['workflow_id']), None)
            backend.workflow_instructions = (
                'You are the FlowCanvas assistant. A user-attached workflow expresses intent to use that workflow for their task. '
                'For the next task request, use the attached workflow without requiring the user to repeat an explicit run command. '
                'If required inputs are missing, ask only for those inputs, then call run_workflow when ready. '
                'Do not run again for questions about results, explanations, or unrelated conversation. '
                'run_workflow starts an MCP, not another model. Execute its returned instructions yourself in this conversation, using the separately named workflow tools (such as ReadTextFile or Complete), with their original MCP arguments. FlowCanvas binds these tools to the most recently started or resumed card. Do not pass card_id to stage tools and do not use run_workflow to execute those operations. Use list_workflow_runs to inspect bound cards, and run_workflow(card_id) to resume or restart them. Never claim success without acceptance. '
                'You cannot modify the whitelist. Authorized workflow: ' +
                (allowed['workflow_id'] if allowed else 'none') + '. ' +
                ('User-attached workflow metadata: ' + json.dumps({
                    'id': allowed['workflow_id'], 'name': workflow_meta['name'],
                    'description': workflow_meta['description'],
                    'inputs': {key: allowed[key] for key in ('rtl_file', 'dut') if allowed.get(key)},
                    'required_inputs': ['rtl_file: existing RTL file path', 'dut: module name']
                }, ensure_ascii=False) + '. ' if allowed and workflow_meta else '') +
                'Determine available capabilities from the current runtime sandbox information and actual tool responses. '
                'Workflow execution authorization is checked by the server. '
                'For bug records, first call bug_records(operation=guide); persist descriptions and evidence with that tool. For test-case import, definitions and bindings, first call test_cases(operation=guide), then use test_cases to persist them. For verification groups, functional points, testpoints and source bindings, first call verification_targets(operation=guide) and follow that skill. Use that tool to persist definitions; do not edit the definition file directly. '
                'Answer in the user language.')
            previous_thread = session.get('thread')
            session['thread'] = await backend.open(session['thread'], message.model, mode)
            if session['backend'] == 'codex' and previous_thread and session.get('runtime_permission_policy') != 1:
                # One-time retirement of the old prompt policy, not a mode-specific
                # permission grant. Future switches only change the native sandbox.
                await backend.request('thread/inject_items', {'threadId': session['thread'], 'items': [{
                    'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text':
                        'FlowCanvas configuration migration: earlier FlowCanvas prompt restrictions requiring workflow-only writes/builds/tests are retired. Determine capabilities from current runtime sandbox information and actual tool responses. Workflow authorization is enforced by the server. This message does not grant filesystem or tool permissions.'}]}]})
            session['runtime_permission_policy'] = 1
            self.changed(session)
            if session.get('delegation_stopped') or session['status'] == 'stopping':
                session['status'] = 'error' if session.get('stop_error') else 'interrupted'
                return
            turn = await backend.send(session['thread'], ('Quoted context (reference only):\n' + message.quote + '\n\nUser request:\n' if message.quote else '') + message.text, message.model, message.effort,
                                           session.get('service_tier'), mode)
            session['turn'] = turn
            # Notifications may precede the RPC response: never overwrite terminal state.
            if session.get('delegation_stopped') or session['status'] == 'stopping':
                await backend.interrupt(session['thread'], turn)
        except asyncio.CancelledError:
            session['queue_paused'] = True
            if session['status'] in ACTIVE:
                session.update(status='interrupted', ended_at=time.time())
            raise
        except Exception as exc:
            session['queue_paused'] = True
            if session['status'] in ACTIVE:
                session.update(status='error', error=str(exc))
        finally:
            self.launching.discard(session['id'])
            if session['status'] not in ACTIVE:
                session['ended_at'] = session.get('ended_at') or time.time()
            self.changed(session)
            self.save(session)

    async def close(self):
        """Stop owned jobs and checkpoint partial conversation data on shutdown."""
        self.closing = True
        interrupted = {session['id'] for session in self.sessions.values() if session['status'] in ACTIVE}
        failures = {}
        for session in self.sessions.values():
            session.update(queue_paused=True, delegation_stopped=True)
            session['child_activity_epoch'] = session.get('child_activity_epoch', 0) + 1
        if self.flusher:
            self.flusher.cancel()
            await asyncio.gather(self.flusher, return_exceptions=True)
        for job in self.jobs:
            job.cancel()
        if self.jobs:
            await asyncio.gather(*self.jobs, return_exceptions=True)
        # History requests can own either an execution client or a shared catalog
        # client. Drain both before shutdown; later readers reject without connecting.
        for lock in list(self.connection_locks.values()):
            async with lock:
                pass
        for identity, backend in self.session_backends.items():
            backend.emit = lambda event: None
            try:
                await backend.close()
            except Exception as exc:
                # Continue closing other owned resources, but never checkpoint an
                # unconfirmed command cancellation as a successful interruption.
                failures[identity] = str(exc)
        for backend in self.backends.values():
            await backend.close()
        # Disconnect clients before stopping their MCP servers, including OpenCode's
        # persistent SSE connection. Otherwise graceful shutdown races live requests.
        for bridge in self.workflow_bridges.values():
            await bridge.close()
        for session in self.sessions.values():
            session.update(children_running=0, children_pending=False)
            if session['id'] in interrupted or session['id'] in failures:
                error = failures.get(session['id']) or (session.get('error') if session['status'] == 'error' else '')
                self.event({'kind': 'turn', 'session_id': session['id'], 'thread': session['thread'],
                            'turn': session['turn'], 'status': 'failed' if error else 'interrupted', 'error': error or ''})
            self.save(session)


def routes(service):
    """Expose a backend-neutral local chat API and reconnectable event stream."""
    router = APIRouter(prefix='/api/chat')

    @router.get('/backends')
    async def backends(session_id: str | None = None):
        """Discover implemented backends and live model capabilities."""
        workspace = ((service.get(session_id).get('workspace') if session_id else
                      (service.projects.current or {}).get('path')) or service.workspace)
        async def catalog(identity):
            """A missing or disconnected backend must not hide another usable backend."""
            try:
                models = await service.models(identity, workspace, refresh=True)
                return {'id': identity, 'name': NAMES[identity], 'models': models}
            except Exception as exc:
                return {'id': identity, 'name': NAMES[identity], 'models': [], 'error': str(exc)}
        return await asyncio.gather(*(catalog(key) for key in service.backends))

    @router.get('/workflow-runs')
    async def workflow_runs():
        """Expose workspace-wide cards to the UI without expanding model permissions."""
        return sorted([{**card, 'conversation_id': owner['id'],
                        'conversation_title': owner.get('title') or '新对话',
                        'model': owner.get('model'), 'effort': owner.get('effort'), 'backend': owner.get('backend')}
                       for owner in service.sessions.values()
                       if owner.get('project_root') == (service.projects.current or {}).get('path')
                       for card in owner.get('workflow_runs', [])],
                      key=lambda card: card.get('started_at', 0), reverse=True)

    @router.post('/sessions/{identity}/workflow-runs/{card_id}/dismiss')
    async def dismiss_workflow(identity: str, card_id: str):
        """Remove a completed card from the UI while retaining execution evidence."""
        owner = service.get(identity)
        card = next((c for c in owner.get('workflow_runs', []) if c['id'] == card_id), None)
        if card is None:
            raise HTTPException(404, 'Workflow card not found')
        if card['status'] != 'completed':
            raise HTTPException(409, 'Only completed workflow cards can be removed')
        card['dismissed'] = True
        service.changed(owner)
        service.save(owner)
        return {'removed': card_id}

    @router.get('/sessions')
    async def sessions():
        """List lightweight session summaries without loading all messages."""
        return [{**{k: s[k] for k in ('id', 'title', 'status', 'updated', 'backend')},
                 'archived': s.get('archived', False), 'pinned': s.get('pinned', False)}
                for s in sorted(service.sessions.values(), key=lambda s: s['updated'], reverse=True)
                if s.get('project_root') == (service.projects.current or {}).get('path')]

    @router.post('/sessions')
    async def create(value: NewSession):
        """Create a new independently addressable conversation."""
        return service.create(value.backend)

    @router.post('/sessions/{identity}/backend')
    async def change_backend(identity: str, value: NewSession):
        """Only an untouched conversation may change its native history owner."""
        session = service.get(identity)
        if session['thread'] or session['items'] or session.get('queue') or identity in service.launching:
            raise HTTPException(409, 'Start a new conversation to change backend')
        session.update(backend=value.backend, model='', effort='')
        service.changed(session)
        service.save(session)
        return session

    @router.post('/sessions/{identity}/reconnect')
    async def reconnect(identity: str):
        """Reload an idle native connection on its next turn while retaining its history."""
        session = service.get(identity)
        async with service.reconfigure(session):
            pass
        return session

    @router.get('/sessions/{identity}')
    async def get(identity: str):
        """Read persisted UI state without resuming or invoking the model."""
        return service.get(identity)

    @router.get('/sessions/{identity}/subagents')
    async def subagents(identity: str, selected: str | None = None):
        """Read child conversations belonging to this session without starting a turn."""
        from .subagents import read_agents
        session = service.get(identity)
        try:
            return await read_agents(service, session, selected)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from None

    @router.post('/sessions/{identity}/delete')
    async def delete(identity: str):
        """Permanently delete FlowCanvas history, leaving native backend storage untouched."""
        session = service.get(identity)
        async with service.reconfigure(session, workflow=True, deleting=True):
            (service.root / (identity + '.json')).unlink(missing_ok=True)
            service.dirty.discard(identity)
            del service.sessions[identity]
        return {'deleted': identity}

    @router.post('/sessions/{identity}/permissions')
    async def permissions(identity: str, settings: PermissionSettings):
        """Change policy only between turns; clients cannot mutate an executing turn."""
        session = service.get(identity)
        # A loaded native thread retains its old developer instructions on resume.
        # Reconnect cold so the persisted thread resumes with the new policy, while
        # keeping the conversation-owned workflow hosts and cards intact.
        async with service.reconfigure(session):
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

    @router.post('/sessions/{identity}/attachment')
    async def attach(identity: str, value: WorkflowInput):
        """Grant only the explicitly selected enabled workflow to this conversation."""
        session = service.get(identity)
        if session.get('permission_mode', 'workflow') == 'readonly':
            raise HTTPException(403, 'Read-only conversations cannot attach executable workflows')
        resolved = attachment(value)
        async with service.reconfigure(session, workflow=True):
            session['attachment'] = resolved
            service.changed(session)
            service.save(session)
        return session

    @router.post('/sessions/{identity}/attachment/remove')
    async def detach(identity: str):
        """Revoke workflow authorization without deleting its recorded result."""
        session = service.get(identity)
        async with service.reconfigure(session, workflow=True):
            removed = session.pop('attachment', None)
            for card in session.get('workflow_runs', []):
                if not removed or card['workflow_id'] == removed['workflow_id']:
                    card['authorized'] = False
            service.changed(session)
            service.save(session)
        return session

    @router.post('/workflows/direct')
    async def direct(value: DirectWorkflow):
        """Create a new bound conversation and immediately launch its chosen workflow."""
        resolved = attachment(value)
        models = await service.models(value.backend, (service.projects.current or {}).get('path') or service.workspace)
        model = next((row for row in models if row['id'] == value.model), None)
        if not model or value.effort not in model['efforts']:
            raise HTTPException(400, 'Select a valid model and effort')
        session = service.create(value.backend)
        session['attachment'] = resolved
        session['title'] = resolved['name']
        return await send(session['id'], Message(text='Run the attached workflow and report the result in Chinese.', model=value.model, effort=value.effort))

    @router.post('/sessions/{identity}/messages', status_code=202)
    async def send(identity: str, message: Message):
        """Accept one turn atomically; reject duplicates while a turn is active."""
        session = service.get(identity)
        backend = session['backend']
        models = await service.models(backend, session.get('workspace') or service.workspace)
        if service.get(identity) is not session or session['backend'] != backend:
            raise HTTPException(409, 'Conversation backend changed; select its current model')
        if identity in service.deleting:
            raise HTTPException(409, 'Conversation is being deleted')
        model = next((m for m in models if m['id'] == message.model), None)
        if not model or message.effort not in model['efforts']:
            raise HTTPException(400, 'Select a model and reasoning effort from the current catalog')
        if message.speed != 'standard' and message.speed not in model.get('speed_tiers', {}):
            raise HTTPException(400, 'Selected speed tier is not available for this model')
        if not message.text.strip():
            raise HTTPException(400, 'Message is empty')
        tier = model.get('speed_tiers', {}).get(message.speed)
        if session['status'] in ACTIVE or session.get('queue') or identity in service.launching or identity in service.steering:
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
                await service.session_backends[identity].steer(session['thread'], session['turn'], text)
            except Exception as exc:
                raise HTTPException(409, str(exc)) from exc
            finally:
                service.steering.discard(identity)
            session['items'].append({'id': uuid.uuid4().hex, 'kind': 'user', 'text': message.text,
                                     'task_card': message.task_card.model_dump() if message.task_card else None, 'quote': message.quote, 'steered': True, 'status': 'completed',
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
        session['delegation_stopped'] = True
        session['child_activity_epoch'] = session.get('child_activity_epoch', 0) + 1
        session.update(children_running=0, children_pending=False)
        service.catalog_reconnect.discard(identity)
        if session['status'] in ACTIVE:
            session['status'] = 'stopping'
        service.changed(session)
        bridge = service.workflow_bridges.get(identity)
        errors = []
        if bridge:
            try:
                await bridge.runtime.pause()
            except Exception as exc:
                errors.append(str(exc) or type(exc).__name__)
        if bridge and bridge.call:
            bridge.call.cancel()
        service.changed(session)
        try:
            await interrupt_children(service, session)
        except Exception as exc:
            errors.append(str(exc) or type(exc).__name__)
        # Failed child discovery or workflow cleanup must not leave the parent
        # running. Attempt every owner, then preserve any unconfirmed cancellation.
        if session['status'] in ACTIVE and session['turn']:
            try:
                await service.session_backends[identity].interrupt(session['thread'], session['turn'])
            except Exception as exc:
                errors.append(str(exc) or type(exc).__name__)
        if errors:
            if session['status'] == 'error' and session.get('error'):
                errors.append(session['error'])
            session['stop_error'] = '; '.join(dict.fromkeys(errors))
            service.event({'kind': 'turn', 'session_id': identity, 'thread': session['thread'],
                           'turn': session['turn'], 'status': 'failed', 'error': session['stop_error']})
            raise HTTPException(502, session['stop_error'])
        service.save(session)
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
