"""Owned OpenCode HTTP server with native history and normalized streaming events."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import time
from urllib.parse import quote
from urllib.request import getproxies
import uuid

import aiohttp


def permissions(mode):
    """Enforce read-only tools without claiming an OS sandbox OpenCode does not supply."""
    if mode not in {'full', 'workflow', 'readonly'}:
        raise ValueError('Unknown permission mode')
    rules = [{'permission': '*', 'pattern': '*', 'action': 'allow' if mode == 'full' else 'deny'}]
    if mode != 'full':
        rules += [{'permission': name, 'pattern': '*', 'action': 'allow'} for name in
                  ('read', 'glob', 'grep', 'list', 'webfetch', 'websearch', 'codesearch', 'todowrite', 'ucagent_*')]
    # Questions are ordinary assistant messages in FlowCanvas; never strand a native dialog.
    rules.append({'permission': 'question', 'pattern': '*', 'action': 'deny'})
    return rules


def item(part, completed=False):
    """Project one native text/reasoning/tool part without leaking protocol-only records."""
    kind = part.get('type')
    if kind not in {'text', 'reasoning', 'tool'} or part.get('ignored'):
        return None
    state = part.get('state', {})
    timing = state.get('time', {}) if kind == 'tool' else part.get('time', {})
    status = state.get('status') if kind == 'tool' else ('completed' if completed or timing.get('end') else 'running')
    result = {'id': part['id'], 'kind': kind, 'status': {'pending': 'running', 'error': 'failed'}.get(status, status),
              'text': part.get('text', ''), 'title': '', 'input': ''}
    if kind == 'tool':
        arguments = state.get('input', {})
        result.update(title=arguments.get('command') or part.get('tool', 'tool'),
                      input=arguments.get('command') or json.dumps(arguments, ensure_ascii=False, indent=2),
                      text=state.get('output') or state.get('error') or state.get('metadata', {}).get('output', ''))
    if timing.get('start'):
        result['started_at'] = timing['start'] / 1000
    if timing.get('end'):
        result['ended_at'] = timing['end'] / 1000
    if kind == 'reasoning':
        result['parts'] = {'0': result['text']}
    return result


class OpenCodeBackend:
    """Isolate MCP capabilities per conversation while reusing local provider credentials."""

    def __init__(self, workspace, emit):
        """Keep credentials in process memory and bind the server to loopback only."""
        self.workspace, self.emit = Path(workspace), emit
        self.process = self.client = None
        self.url = None
        self.mcp_url = self.workflow_instructions = None
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.active = {}
        self.inputs = {}
        self.parts = {}
        self.messages = {}
        self.message_turns = {}
        self.selections = {}
        self.stopping = set()
        self.ready = asyncio.Event()
        self.closing = False
        self.recovering = set()
        self.refreshing = {}
        self.refresh_pending = {}
        self.snapshot_lock = asyncio.Lock()
        self.delivery_lock = asyncio.Lock()
        self.interrupting = set()

    async def connect(self):
        """Start a private authenticated server and subscribe before accepting prompts."""
        async with self.lock:
            if self.process and self.process.returncode is None and self.ready.is_set():
                return
            await self.shutdown()
            self.closing = False
            executable = shutil.which('opencode')
            if not executable:
                candidate = Path.home() / '.opencode/bin/opencode'
                executable = str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
            if not executable:
                raise RuntimeError('OpenCode CLI not found. Install OpenCode and configure a provider first.')
            password = secrets.token_urlsafe(32)
            env = dict(os.environ, OPENCODE_SERVER_PASSWORD=password, OPENCODE_SERVER_USERNAME='flowcanvas')
            # Desktop launches may have OS proxy settings without shell variables.
            # Explicit choices (including empty values) win; local MCP stays direct.
            if not any(key in env for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                                              'http_proxy', 'https_proxy', 'all_proxy')):
                for scheme, proxy in getproxies().items():
                    if scheme in {'http', 'https', 'all'}:
                        env[scheme.upper() + '_PROXY'] = proxy
            bypass = env.get('no_proxy', env.get('NO_PROXY', '')).split(',')
            env['NO_PROXY'] = env['no_proxy'] = ','.join(dict.fromkeys(
                [entry.strip() for entry in bypass if entry.strip()] + ['localhost', '127.0.0.1', '::1']))
            env['OPENCODE_CONFIG_CONTENT'] = json.dumps({'share': 'disabled', 'autoupdate': False,
                                                        'permission': 'deny'})
            self.process = await asyncio.create_subprocess_exec(
                executable, 'serve', '--port', '0', '--hostname', '127.0.0.1', '--pure',
                cwd=self.workspace, env=env, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            try:
                async with asyncio.timeout(30):
                    while line := await self.process.stdout.readline():
                        text = line.decode(errors='replace').strip()
                        if 'http://127.0.0.1:' in text:
                            self.url = 'http://127.0.0.1:' + text.split('http://127.0.0.1:', 1)[1]
                            break
                    else:
                        raise RuntimeError('OpenCode server exited during startup.')
                self.client = aiohttp.ClientSession(
                    headers={'Authorization': aiohttp.encode_basic_auth('flowcanvas', password)},
                    timeout=aiohttp.ClientTimeout(total=45))
                # The user provider/model configuration is reused; external MCPs are not
                # implicitly granted to a FlowCanvas conversation.
                for name in await self.http('GET', '/mcp'):
                    await self.http('POST', '/mcp/' + quote(name, safe='') + '/disconnect', {})
                if self.mcp_url:
                    # Startup already owns the lifecycle lock; taking the delivery
                    # lock here would invert submit-failure -> close lock ordering.
                    await self.register_tools()
                self.background(self.read())
                await asyncio.wait_for(self.ready.wait(), 15)
            except BaseException:
                await self.shutdown()
                raise

    def background(self, coroutine):
        """Own asynchronous readers and bounded native-dialog rejections until shutdown."""
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def http(self, method, path, body=None, *, workspace=None):
        """Use a bounded native request without exposing provider configuration in errors."""
        if not self.client or self.client.closed:
            raise RuntimeError('OpenCode connection closed')
        try:
            async with self.client.request(method, self.url + path, json=body,
                                           params={'directory': str(workspace or self.workspace)}) as response:
                if response.status >= 400:
                    raise RuntimeError(f'OpenCode request failed ({response.status}): {method} {path}')
                return await response.json() if response.content_type == 'application/json' else None
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RuntimeError(f'OpenCode connection failed: {method} {path}') from exc

    async def models(self, workspace=None, refresh=False):
        """Read connected models and defaults from the requested project's native config."""
        await self.connect()
        if refresh:
            if self.active:
                raise RuntimeError('Cannot refresh configuration on an active execution connection')
            # FlowCanvas uses a separate discovery process for this operation.
            # Native config is cached per directory until its instance is disposed.
            await self.http('POST', '/instance/dispose', workspace=workspace)
        config, providers = await asyncio.gather(self.http('GET', '/config', workspace=workspace),
                                                self.http('GET', '/provider', workspace=workspace))
        result = []
        for provider in providers['all']:
            if provider['id'] not in providers['connected']:
                continue
            for key, model in provider.get('models', {}).items():
                if not model.get('capabilities', {}).get('toolcall', False):
                    continue
                identity = provider['id'] + '/' + key
                variants = list(model.get('variants', {}))
                result.append({'id': identity, 'name': model.get('name', key) + ' · ' + provider['id'],
                               'default': identity == config.get('model'), 'efforts': ['default', *variants],
                               'default_effort': 'default', 'speed_tiers': {}})
        return sorted(result, key=lambda row: (not row['default'], row['name'].casefold()))

    async def open(self, thread_id, model, mode='workflow'):
        """Resume native history with the current tool policy, or create a fresh session."""
        await self.connect()
        policy = permissions(mode)
        if thread_id:
            await self.http('PATCH', '/session/' + thread_id, {'permission': policy})
        else:
            thread_id = (await self.http('POST', '/session', {'title': 'FlowCanvas', 'permission': policy}))['id']
        return thread_id

    async def send(self, thread_id, text, model, effort, service_tier=None, mode='workflow'):
        """Submit asynchronously; one native user message identifies a FlowCanvas turn."""
        if service_tier:
            raise ValueError('OpenCode does not expose Codex service tiers')
        if thread_id in self.active:
            raise RuntimeError('OpenCode session is already running')
        await self.http('PATCH', '/session/' + thread_id, {'permission': permissions(mode)})
        provider, model_id = model.split('/', 1)
        selection = {'model': {'providerID': provider, 'modelID': model_id}, 'agent': 'build',
                     'system': (self.workflow_instructions or 'You are the FlowCanvas assistant.') +
                     ' Ask questions in your response text; the interactive question tool is unavailable.'
                     ' Cite inspected local source with clickable Markdown links: '
                     '[file.ext:12](/absolute/path/file.ext#L12), or #L12-L18 for a range. '
                     'Use the actual absolute file path and verified line numbers; URL-encode spaces and '
                     'special characters in link targets. Keep the link itself outside code formatting.'}
        if effort != 'default':
            selection['variant'] = effort
        self.selections[thread_id] = selection
        turn = 'msg_' + format(int(time.time() * 1000), '012x') + uuid.uuid4().hex[:16]
        self.messages[turn] = {'role': 'user'}
        self.message_turns[turn] = turn
        self.active[thread_id] = turn
        self.inputs[thread_id] = turn
        self.stopping.discard(thread_id)
        self.emit({'kind': 'turn', 'thread': thread_id, 'turn': turn, 'status': 'inProgress', 'error': ''})
        await self.submit(thread_id, turn, {
            **selection, 'messageID': turn, 'parts': [{'type': 'text', 'text': text}]})
        return turn

    async def submit(self, thread, turn, payload):
        """Send once; recover an accepted message or cancel an unconfirmed submission."""
        async with self.delivery_lock:
            if thread in self.stopping or self.closing:
                raise RuntimeError('OpenCode task is stopping')
            identity = payload['messageID']
            try:
                await self.http('POST', '/session/' + thread + '/prompt_async', payload)
            except asyncio.CancelledError:
                # Local cancellation is not native cancellation. Shutdown still needs
                # the active owner in order to abort commands already running there.
                raise
            except Exception as exc:
                if identity == turn and self.active.get(thread) != turn:
                    return  # A native terminal event already settled this exact turn.
                try:
                    async with asyncio.timeout(10):
                        while True:
                            if identity == turn and self.active.get(thread) != turn:
                                return
                            try:
                                receipt = await self.http('GET', '/session/' + thread + '/message/' + identity)
                            except RuntimeError:
                                if not self.process or self.process.returncode is not None:
                                    break
                                # prompt_async can acknowledge before its fork has
                                # persisted the user message. Query that same ID only.
                                await asyncio.sleep(.1)
                                continue
                            info = receipt['info']
                            if info['id'] == identity and info['sessionID'] == thread and info['role'] == 'user':
                                self.messages[identity] = info
                                self.message_turns[identity] = turn
                                return
                            break
                except (RuntimeError, TimeoutError, KeyError, TypeError):
                    pass
                if identity == turn and self.active.get(thread) != turn:
                    return
                error = 'OpenCode submission could not be confirmed; it was not resent.'
                if self.active.get(thread) == turn:
                    try:
                        await self.close()
                    except Exception:
                        error += ' Native cancellation could not be confirmed.'
                    self.emit({'kind': 'turn', 'thread': thread, 'turn': turn, 'status': 'failed', 'error': error})
                raise RuntimeError(error) from exc

    async def steer(self, thread_id, turn_id, text):
        """Append native guidance for the next model step without interrupting active tools."""
        if self.active.get(thread_id) != turn_id or thread_id in self.stopping or self.closing:
            raise RuntimeError('The OpenCode turn is no longer accepting guidance')
        identity = 'msg_' + format(int(time.time() * 1000), '012x') + uuid.uuid4().hex[:16]
        self.message_turns[identity] = turn_id
        self.inputs[thread_id] = identity
        await self.submit(thread_id, turn_id, {
            **self.selections[thread_id], 'messageID': identity, 'parts': [{'type': 'text', 'text': text}]})

    async def interrupt(self, thread_id, turn_id):
        """Cancel execution and pending native preparation while retaining the session history."""
        if thread_id in self.active and self.active[thread_id] != turn_id:
            return
        await self.cancel_turn(thread_id, status='interrupted')

    async def reload_tools(self):
        """Refresh MCP only between turns; active workflows use the existing catalog handoff."""
        # Native tool closures retain their client for the current model step.
        # Replacing that client mid-turn makes an already selected tool fail.
        async with self.delivery_lock:
            if self.mcp_url and not self.active and not self.closing:
                await self.register_tools()

    async def register_tools(self):
        """Install the owned MCP endpoint during serialized startup or an idle refresh."""
        result = await self.http('POST', '/mcp', {'name': 'ucagent', 'config': {
            'type': 'remote', 'url': self.mcp_url, 'enabled': True, 'oauth': False, 'timeout': 1860000}})
        if result.get('ucagent', {}).get('status') != 'connected':
            raise RuntimeError('OpenCode could not connect to the FlowCanvas tools')

    async def reject_dialog(self, kind, identity):
        """Reject unsupported interactive dialogs rather than hanging the conversation."""
        try:
            await self.http('POST', '/' + kind + '/' + identity + ('/reply' if kind == 'permission' else '/reject'),
                            {'reply': 'reject'} if kind == 'permission' else {})
        except RuntimeError:
            pass  # A dialog already answered or cancelled has no outstanding work.

    async def read(self):
        """Consume SSE frames and reconcile after transport reconnects before continuing."""
        while not self.closing:
            try:
                async with self.client.get(self.url + '/event', params={'directory': str(self.workspace)},
                                           timeout=aiohttp.ClientTimeout(total=None, sock_read=90)) as response:
                    response.raise_for_status()
                    self.ready.set()
                    # There is no cursor linking v1 snapshots to buffered SSE deltas.
                    # After a gap, use coalesced full-message snapshots for this turn;
                    # appending those deltas could duplicate already recovered text.
                    self.recovering.update(self.active)
                    for thread in list(self.active):
                        await self.reconcile(thread)
                        states = await self.http('GET', '/session/status')
                        if states.get(thread, {}).get('type', 'idle') == 'idle':
                            await self.settle(thread)
                    data, pending = [], bytearray()
                    # A tool snapshot can exceed the HTTP reader's line limit.
                    # Frame bytes ourselves; decode only complete protocol lines so
                    # split UTF-8 characters and multi-line SSE data stay intact.
                    async for chunk in response.content.iter_any():
                        fragments = chunk.split(b'\n')
                        for index, fragment in enumerate(fragments):
                            pending.extend(fragment)
                            if index == len(fragments) - 1:
                                continue
                            line = pending.decode().rstrip('\r'); pending.clear()
                            if line.startswith('data:'):
                                data.append(line[5:].lstrip())
                            elif not line and data:
                                event = json.loads('\n'.join(data)); data.clear()
                                await self.event(event)
                if self.closing:
                    return
            except (AttributeError, KeyError, TypeError):
                self.closing = True
                self.ready.clear()
                error = 'OpenCode sent an unsupported event. Refresh the backend connection.'
                # A fatal reader must cancel native work before releasing its
                # owner. Drain any in-flight delivery so it cannot restart work
                # after cancellation, and keep this reader out of its own cleanup.
                try:
                    async with self.delivery_lock:
                        await self.close()
                except Exception:
                    error += ' Native cancellation could not be confirmed.'
                self.emit({'kind': 'disconnected', 'error': error})
                return
            except (aiohttp.ClientError, TimeoutError, ValueError, RuntimeError):
                if self.closing:
                    return
            if self.process.returncode is not None:
                self.emit({'kind': 'disconnected', 'error': 'OpenCode stopped; send a new message to reconnect.'})
                return
            await asyncio.sleep(.5)

    async def event(self, event):
        """Apply snapshots and deltas once; native idle, not an assistant part, ends a turn."""
        kind, props = event.get('type'), event.get('properties', {})
        if kind in {'permission.asked', 'question.asked'}:
            self.background(self.reject_dialog(kind.split('.')[0], props['id']))
            return
        thread = props.get('sessionID')
        if kind == 'message.updated':
            info = props['info']; self.messages[info['id']] = info
            if not await self.owns_message(thread, info['id']):
                return
            if info.get('role') == 'assistant' and thread in self.active:
                self.project_usage(thread, self.active[thread], info)
            if info.get('error') and thread in self.active:
                error = info['error']
                if error.get('name') == 'MessageAbortedError':
                    self.stopping.add(thread)
                else:
                    await self.cancel_turn(thread, error.get('data', {}).get('message') or error.get('name', 'OpenCode error'))
        elif kind == 'message.part.updated':
            part = props['part']
            if thread in self.recovering:
                self.queue_refresh(thread, part['messageID'])
                return
            self.parts[part['id']] = part
            if (await self.owns_message(thread, part['messageID'])
                    and self.messages.get(part['messageID'], {}).get('role') == 'assistant'
                    and not self.messages[part['messageID']].get('summary')):
                block = item(part)
                if block:
                    self.emit({'kind': 'item', 'thread': thread, 'turn': self.active[thread], 'item': block})
        elif kind == 'message.part.delta':
            if thread in self.recovering:
                self.queue_refresh(thread, props['messageID'])
                return
            part = self.parts.get(props['partID'])
            if part and props['field'] == 'text':
                part['text'] = part.get('text', '') + props['delta']
                if (await self.owns_message(thread, props['messageID'])
                        and self.messages.get(props['messageID'], {}).get('role') == 'assistant'
                        and not self.messages[props['messageID']].get('summary')):
                    self.emit({'kind': 'delta', 'thread': thread, 'turn': self.active[thread],
                               'id': part['id'], 'type': part['type'], 'text': props['delta'], 'part': 0})
        elif kind == 'session.error' and thread in self.active:
            error = props.get('error', {})
            # Native overflow signals can start automatic compaction. Only a
            # persisted assistant error makes overflow terminal (including an
            # explicitly disabled recovery or a failed compaction summary).
            if error.get('name') == 'ContextOverflowError':
                return
            if error.get('name') == 'MessageAbortedError':
                self.stopping.add(thread)
            else:
                await self.cancel_turn(thread, error.get('data', {}).get('message') or error.get('name', 'OpenCode error'))
        elif kind == 'session.idle' or (kind == 'session.status' and props['status']['type'] == 'idle'):
            await self.settle(thread)
        elif kind == 'session.status' and thread in self.active and props['status']['type'] == 'retry':
            self.emit({'kind': 'error', 'thread': thread, 'turn': self.active[thread], 'retrying': True,
                       'error': props['status'].get('message', 'OpenCode is retrying the request')})

    def remember_history(self, records, turn):
        """Index native parent ownership without replaying buffered message text."""
        present = False
        for record in records:
            info = record['info']; identity = info['id']
            self.messages.setdefault(identity, info)
            if identity == turn:
                present = True
            parent = self.message_turns.get(info.get('parentID'))
            self.message_turns.setdefault(identity, parent if parent is not None else turn if present else '')
        return present

    async def owns_message(self, thread, identity):
        """Exclude previous-turn events while including native steering and compaction users."""
        turn = self.active.get(thread)
        if not turn:
            return False
        info = self.messages.get(identity, {})
        owner = self.message_turns.get(identity, self.message_turns.get(info.get('parentID')))
        if owner is None and (identity == turn or info.get('parentID') == turn):
            owner = turn
        if owner is None:
            # Synthetic continuation users have no parent. Resolve their place in
            # native history, without projecting snapshots ahead of buffered deltas.
            records = await self.http('GET', '/session/' + thread + '/message')
            if self.active.get(thread) != turn:
                return False
            self.remember_history(records, turn)
            owner = self.message_turns.get(identity)
        if owner is not None:
            self.message_turns[identity] = owner
        return owner == turn and self.active.get(thread) == turn

    def queue_refresh(self, thread, message):
        """Coalesce reconnect updates without repeatedly downloading full conversation history."""
        turn = self.active.get(thread)
        if not turn:
            return
        self.refresh_pending.setdefault(thread, set()).add(message)
        previous = self.refreshing.get(thread)
        if previous and previous[0] != turn:
            previous[1].cancel()
        if not previous or previous[0] != turn or previous[1].done():
            self.refreshing[thread] = (turn, self.background(self.refresh_messages(thread, turn)))

    async def refresh_messages(self, thread, turn):
        """Refresh changed native messages serially so stale responses cannot rewind output."""
        try:
            while self.active.get(thread) == turn and self.refresh_pending.get(thread):
                await asyncio.sleep(.15)
                async with self.snapshot_lock:
                    pending = self.refresh_pending.pop(thread, set())
                    for message in pending:
                        try:
                            record = await self.http('GET', '/session/' + thread + '/message/' + message)
                            self.messages.setdefault(record['info']['id'], record['info'])
                            owned = record['info']['role'] == 'assistant' and await self.owns_message(thread, record['info']['id'])
                        except RuntimeError:
                            if self.active.get(thread) == turn:
                                self.refresh_pending.setdefault(thread, set()).add(message)
                            continue
                        if self.active.get(thread) != turn:
                            return
                        if owned:
                            self.project_record(thread, turn, record)
        finally:
            previous = self.refreshing.get(thread)
            if previous and previous[1] is asyncio.current_task():
                self.refreshing.pop(thread, None)

    def project_usage(self, thread, turn, info):
        """Restore inclusive token categories for one native message, not a task aggregate."""
        tokens = info.get('tokens')
        if tokens is None:
            return
        cache = tokens.get('cache', {})
        cached, written = cache.get('read', 0), cache.get('write', 0)
        reasoning = tokens.get('reasoning', 0)
        # OpenCode splits caches out of input and reasoning out of output for
        # pricing. Our Codex-shaped totals include those respective subsets.
        incoming = tokens.get('input', 0) + cached + written
        outgoing = tokens.get('output', 0) + reasoning
        total = tokens.get('total')
        self.emit({'kind': 'usage', 'thread': thread, 'turn': turn, 'usage': {
            'last': {'inputTokens': incoming, 'outputTokens': outgoing,
                     'cachedInputTokens': cached, 'cacheWriteInputTokens': written,
                     'reasoningOutputTokens': reasoning,
                     'totalTokens': incoming + outgoing if total is None else total},
            'cost': info.get('cost', 0)}})

    def project_record(self, thread, turn, record):
        """Replace native part snapshots using stable IDs, never concatenating recovered text."""
        info = record['info']
        self.messages[info['id']] = info
        self.project_usage(thread, turn, info)
        # Compaction is internal context maintenance. Its usage and errors still
        # flow through the normal lifecycle, but its text is not a user reply.
        if info.get('summary'):
            return
        for part in record['parts']:
            self.parts[part['id']] = part
            block = item(part, bool(info.get('time', {}).get('completed')))
            if block:
                self.emit({'kind': 'item', 'thread': thread, 'turn': turn, 'item': block})

    async def reconcile(self, thread):
        """Refresh owned snapshots and prove the latest submitted input has a final answer."""
        turn = self.active.get(thread)
        async with self.snapshot_lock:
            records = await self.http('GET', '/session/' + thread + '/message')
            if not turn or self.active.get(thread) != turn:
                return False
            present = self.remember_history(records, turn)
            failure = None
            expected = self.inputs.get(thread, turn)
            accepted = False
            last_user = last_answer = None
            for record in records:
                info = record['info']; self.messages[info['id']] = info
                if self.message_turns.get(info['id']) != turn:
                    continue
                if info['role'] == 'user':
                    last_user = info
                    accepted |= info['id'] == expected
                    continue
                if info['role'] != 'assistant':
                    continue
                last_answer = info
                if info.get('error'):
                    failure = info['error']
                self.project_record(thread, turn, record)
            if failure:
                if failure.get('name') == 'MessageAbortedError':
                    self.stopping.add(thread)
                else:
                    await self.cancel_turn(thread, failure.get('data', {}).get('message') or failure.get('name', 'OpenCode error'))
            # Native idle can belong to the previous reply while guidance is
            # still being submitted. A tool round or internal summary is not the
            # final answer to the latest user input (including native continuations).
            return bool(present and accepted and last_user and last_answer
                        and last_answer.get('parentID') == last_user['id']
                        and last_answer.get('time', {}).get('completed')
                        and last_answer.get('finish') not in {None, 'tool-calls', 'unknown'}
                        and not last_answer.get('summary'))

    async def settle(self, thread):
        """Use native messages to settle complete output before emitting terminal status."""
        if thread not in self.active or thread in self.interrupting:
            return
        turn = self.active[thread]
        expected = self.inputs.get(thread, turn)
        answered = await self.reconcile(thread)
        states = await self.http('GET', '/session/status')
        if (self.active.get(thread) == turn and self.inputs.get(thread, turn) == expected
                and thread not in self.interrupting
                and (answered or thread in self.stopping)
                and states.get(thread, {}).get('type', 'idle') == 'idle'):
            self.finish(thread, 'interrupted' if thread in self.stopping else 'completed')

    async def cancel_turn(self, thread, error='', *, status='failed'):
        """Retire the execution connection after draining delivery and cancelling native work."""
        turn = self.active.get(thread)
        if not turn:
            return
        self.stopping.add(thread)
        self.interrupting.add(thread)
        try:
            async with self.delivery_lock:
                if self.active.get(thread) != turn:
                    return
                try:
                    await self.http('POST', '/session/' + thread + '/abort', {})
                except (RuntimeError, TimeoutError):
                    pass  # close retries cancellation while HTTP is still usable.
                # prompt_async preparation is outside the native execution loop;
                # abort can return idle before that preparation starts a new loop.
                # Reap this conversation's server as well, preserving its history.
                try:
                    await self.close()
                except Exception:
                    status = 'failed'
                    error = (error + ' ' if error else '') + 'Native cancellation could not be confirmed.'
                self.emit({'kind': 'turn', 'thread': thread, 'turn': turn, 'status': status, 'error': error})
        finally:
            self.interrupting.discard(thread)

    def finish(self, thread, status, error=''):
        """Emit a single terminal event for the owned active turn."""
        turn = self.active.pop(thread, None)
        if turn:
            self.inputs.pop(thread, None)
            self.recovering.discard(thread)
            self.refresh_pending.pop(thread, None)
            self.emit({'kind': 'turn', 'thread': thread, 'turn': turn, 'status': status, 'error': error})

    async def agents(self, thread, selected=None, include_messages=True):
        """Traverse native parent links, limiting detail reads to authorized descendants."""
        await self.connect()
        agents, seen, frontier = [], {thread}, [thread]
        statuses = await self.http('GET', '/session/status')
        histories = {}
        while frontier:
            parent = frontier.pop(0)
            for child in await self.http('GET', '/session/' + parent + '/children'):
                identity = child['id']
                if identity in seen:
                    continue
                seen.add(identity); frontier.append(identity)
                records = await self.http('GET', '/session/' + identity + '/message')
                histories[identity] = records
                last = next((row['info'] for row in reversed(records) if row['info']['role'] == 'assistant'), {})
                state = statuses.get(identity, {}).get('type', 'idle')
                error = last.get('error', {})
                status = ('running' if state in {'busy', 'retry'} else 'interrupted' if error.get('name') == 'MessageAbortedError'
                          else 'error' if error else 'completed' if last.get('time', {}).get('completed') else 'idle')
                task = next(('\n'.join(p.get('text', '') for p in row['parts'] if p['type'] == 'text')
                             for row in records if row['info']['role'] == 'user'), '')
                agents.append({'id': identity, 'name': child.get('title') or 'OpenCode agent',
                               'task': task, 'status': status, 'model': last.get('modelID')})
        identity = selected if selected in histories else (agents[0]['id'] if agents else None)
        blocks = []
        if identity and include_messages:
            for record in histories[identity]:
                if record['info']['role'] == 'user':
                    text = '\n'.join(p.get('text', '') for p in record['parts']
                                     if p['type'] == 'text' and not p.get('synthetic') and not p.get('ignored'))
                    if text:
                        blocks.append({'id': record['info']['id'], 'kind': 'user', 'text': text})
                elif not record['info'].get('summary'):
                    blocks.extend(block for p in record['parts'] if (block := item(p, bool(record['info'].get('time', {}).get('completed')))))
        return {'agents': agents, 'selected': identity, 'items': blocks}

    async def interrupt_descendants(self, thread):
        """Abort all live native descendants when the owning task is stopped."""
        errors = []
        for row in (await self.agents(thread, include_messages=False))['agents']:
            if row['status'] == 'running':
                try:
                    await self.http('POST', '/session/' + row['id'] + '/abort', {})
                except Exception as exc:
                    errors.append(row['id'] + ': ' + (str(exc) or type(exc).__name__))
        if errors:
            raise RuntimeError('; '.join(errors))

    async def close(self):
        """Serialize connection retirement with observers that may reconnect to read history."""
        async with self.lock:
            await self.shutdown()

    async def shutdown(self):
        """Cancel owned commands before reaping the server, preserving native history."""
        self.closing = True
        # A native-error reader can retire its own connection. It must not cancel
        # or await itself while draining the other owned background work.
        pending = self.tasks - {asyncio.current_task()}
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.tasks.clear()
        failed = False
        if self.process and self.process.returncode is None and self.client and not self.client.closed:
            # The native shell may own a detached process group. Terminating only
            # the HTTP server can leave that command alive; use native cancellation
            # while the connection is still available, with bounded shutdown time.
            for thread in list(self.active):
                try:
                    await asyncio.wait_for(self.http('POST', '/session/' + thread + '/abort', {}), 10)
                except (RuntimeError, TimeoutError):
                    failed = True
        if self.client:
            await self.client.close()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill(); await self.process.wait()
        self.ready.clear()
        self.active.clear()
        self.inputs.clear()
        self.recovering.clear()
        self.refresh_pending.clear()
        self.refreshing.clear()
        if failed:
            raise RuntimeError('OpenCode command cancellation could not be confirmed before shutdown.')
