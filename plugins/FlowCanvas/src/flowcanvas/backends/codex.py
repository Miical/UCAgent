"""Async Codex app-server transport; native protocol stays behind this adapter."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import tomllib


class CodexBackend:
    """Own one native process and deliver normalized events without blocking its reader."""

    def __init__(self, workspace, emit):
        """Bind a fixed work directory and synchronous event consumer."""
        self.workspace, self.emit = workspace, emit
        self.process = None
        self.pending = {}
        self.sequence = 0
        self.lock = asyncio.Lock()
        self.tasks = []
        self.loaded = {}
        self.mcp_url = None
        self.workflow_instructions = None

    async def connect(self):
        """Initialize once; a dead process is replaced on the next user operation."""
        async with self.lock:
            if self.process and self.process.returncode is None:
                return
            executable = shutil.which('codex')
            if not executable:
                raise RuntimeError('Codex CLI not found. Install and sign in with codex login.')
            args = [executable, 'app-server', '--listen', 'stdio://',
                    '-c', 'project_doc_max_bytes=0', '-c', 'features.fast_mode=true',
                    '-c', 'sandbox_workspace_write.network_access=true']
            # Override the entire table: CLI dotted keys do not parse TOML-quoted names.
            # Only our conversation-owned dispatcher or enabled workflow host is
            # trusted here. The dispatcher enforces card ownership and enabled workflows;
            # the workflow host enforces its configured tools and write scope.
            # "never" alone rejects MCP writes instead of authorizing them.
            mcp_config = ('mcp_servers={ucagent={url=' + json.dumps(self.mcp_url)
                          + ', default_tools_approval_mode="approve", tool_timeout_sec=1860}}'
                          if self.mcp_url else 'mcp_servers={}')
            args += ['-c', mcp_config]
            config = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'config.toml'
            if config.is_file():
                data = tomllib.loads(config.read_text())
                plugins = ', '.join(json.dumps(name) + ' = { enabled = false }'
                                    for name in data.get('plugins', {}))
                args += ['-c', 'plugins={' + plugins + '}']
            self.loaded.clear()
            self.process = await asyncio.create_subprocess_exec(
                *args, cwd=self.workspace, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=16 * 1024 * 1024)
            self.tasks.append(asyncio.create_task(self.read(self.process)))
            try:
                await self.request('initialize', {'clientInfo': {
                    'name': 'flowcanvas', 'title': 'UCAgent FlowCanvas', 'version': '0.1.0'}})
                await self.write({'method': 'initialized'})
            except Exception:
                await self.close()
                raise

    async def write(self, message):
        """Write one complete JSON-RPC frame on the event loop."""
        if not self.process or self.process.returncode is not None:
            raise RuntimeError('Codex connection closed')
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        await self.process.stdin.drain()

    async def request(self, method, params):
        """Match a request to its response and bound stalled RPC calls."""
        self.sequence += 1
        key = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.write({'id': key, 'method': method, 'params': params})
            return await asyncio.wait_for(future, 45)
        except TimeoutError as exc:
            raise RuntimeError(f'Codex request timed out: {method}') from exc
        finally:
            self.pending.pop(key, None)

    async def read(self, process):
        """Demultiplex replies, notifications and denied permission requests."""
        try:
            while line := await process.stdout.readline():
                msg = json.loads(line)
                if 'method' not in msg:
                    future = self.pending.get(msg.get('id'))
                    if future and not future.done():
                        if 'error' in msg:
                            future.set_exception(RuntimeError(msg['error'].get('message', 'Codex error')))
                        else:
                            future.set_result(msg.get('result', {}))
                elif 'id' in msg:
                    # Unexpected approval requests remain denied; full mode uses its explicit sandbox policy.
                    if msg['method'].endswith('/requestApproval'):
                        reply = {'result': {'decision': 'decline'}}
                    else:
                        reply = {'error': {'code': -32601, 'message': 'Interactive tool requests are not enabled in this chat.'}}
                    await self.write({'id': msg['id'], **reply})
                else:
                    event = normalize(msg['method'], msg.get('params', {}))
                    if event:
                        self.emit(event)
        except (ValueError, OSError, RuntimeError) as exc:
            self.emit({'kind': 'disconnected', 'error': str(exc)})
        finally:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(RuntimeError('Codex connection closed'))
            self.emit({'kind': 'disconnected', 'error': 'Codex connection closed; send a new message to reconnect.'})
            if process.returncode is None:
                process.terminate()
            await process.wait()

    async def models(self, workspace=None, refresh=False):
        """Return Codex's account-wide catalog; its model API has no directory parameter."""
        await self.connect()
        models, cursor = [], None
        while True:
            result = await self.request('model/list', {'cursor': cursor})
            models.extend(result['data'])
            cursor = result.get('nextCursor')
            if not cursor:
                return [{'id': m['model'], 'name': m['displayName'], 'default': m['isDefault'],
                         'efforts': [e['reasoningEffort'] for e in m['supportedReasoningEfforts']],
                         'default_effort': m['defaultReasoningEffort'],
                         'speed_tiers': {t['name'].lower(): t['id'] for t in m.get('serviceTiers', [])
                                         if t['name'].lower() in {'fast', 'ultrafast'}}} for m in models if not m['hidden']]

    async def open(self, thread_id, model, mode='workspace'):
        """Resume with the selected policy, refreshing instructions on permission changes."""
        if mode not in {'full', 'workspace'}:
            raise ValueError('Unknown permission mode')
        await self.connect()
        if thread_id and self.loaded.get(thread_id) == mode:
            return thread_id
        params = {'cwd': str(self.workspace), 'model': model,
                  'sandbox': 'danger-full-access' if mode == 'full' else 'workspace-write',
                  'approvalPolicy': 'never', 'developerInstructions':
                  self.workflow_instructions or 'You are the FlowCanvas assistant. Use runtime sandbox information and tool responses to determine available capabilities. Answer in the user language.'}
        if thread_id:
            params.update(threadId=thread_id, excludeTurns=True)
        result = await self.request('thread/resume' if thread_id else 'thread/start', params)
        identity = result['thread']['id']
        self.loaded[identity] = mode
        return identity

    async def send(self, thread_id, text, model, effort, service_tier=None, mode='workspace'):
        """Explicitly set every turn's sandbox, including when downgrading from full access."""
        if mode not in {'full', 'workspace'}:
            raise ValueError('Unknown permission mode')
        result = await self.request('turn/start', {'threadId': thread_id,
            'input': [{'type': 'text', 'text': text}], 'model': model, 'effort': effort, 'serviceTier': service_tier,
            'summary': 'auto', 'approvalPolicy': 'never',
            'sandboxPolicy': ({'type': 'dangerFullAccess'} if mode == 'full' else {
                'type': 'workspaceWrite', 'writableRoots': [str(self.workspace)],
                'networkAccess': True, 'excludeTmpdirEnvVar': True, 'excludeSlashTmp': False})})
        return result['turn']['id']

    async def steer(self, thread_id, turn_id, text):
        """Append guidance to the exact active turn, without cancelling or restarting it."""
        await self.request('turn/steer', {'threadId': thread_id, 'expectedTurnId': turn_id,
                                        'input': [{'type': 'text', 'text': text}]})

    async def interrupt(self, thread_id, turn_id):
        """Cancel only the requested native turn."""
        await self.request('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})

    async def reload_tools(self):
        """Refresh the native MCP catalog while retaining the current thread."""
        await self.request('config/mcpServer/reload', None)

    async def agents(self, thread, selected=None, include_messages=True):
        """Read only native descendants of the requested parent thread."""
        from ..subagents import read_codex_agents
        return await read_codex_agents(self, thread, selected, include_messages)

    async def interrupt_descendants(self, thread):
        """Stop actual child turns rather than treating sidebar status as authority."""
        errors = []
        for row in (await self.agents(thread, include_messages=False))['agents']:
            try:
                page = await self.request('thread/turns/list', {
                    'threadId': row['id'], 'limit': 1, 'sortDirection': 'desc', 'itemsView': 'summary'})
                for turn in page.get('data', []):
                    if turn.get('status') == 'inProgress':
                        await self.interrupt(row['id'], turn['id'])
            except Exception as exc:
                errors.append(row['id'] + ': ' + (str(exc) or type(exc).__name__))
        if errors:
            raise RuntimeError('; '.join(errors))

    async def close(self):
        """Reap the owned process during application shutdown."""
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in self.tasks:
            if task is not asyncio.current_task():
                task.cancel()
        self.loaded.clear()


def normalize(method, params):
    """Translate native events into backend-independent chat blocks and turn states."""
    base = {'thread': params.get('threadId'), 'turn': params.get('turnId')}
    if method in ('item/started', 'item/completed'):
        item = params['item']
        typ = item['type']
        if typ in {'userMessage', 'subAgentActivity'}:
            # User bubbles are owned by HTTP; child lifecycle belongs in the agent sidebar.
            return None
        kind = {'agentMessage': 'text', 'reasoning': 'reasoning', 'plan': 'plan'}.get(typ, 'tool')
        text = item.get('text', '')
        if typ == 'reasoning':
            text = '\n\n'.join(item.get('summary', []))
        if kind == 'tool':
            text = item.get('aggregatedOutput') or ''
            if item.get('result'):
                text = json.dumps(item['result'], ensure_ascii=False, indent=2)
            if item.get('error'):
                text = json.dumps(item['error'], ensure_ascii=False)
        return {**base, 'kind': 'item', 'item': {'id': item['id'], 'kind': kind,
                'text': text, 'title': item.get('command') or item.get('tool') or typ,
                'input': item.get('command') or (json.dumps(item['arguments'], ensure_ascii=False, indent=2) if 'arguments' in item else ''),
                'status': item.get('status') or ('completed' if method.endswith('completed') else 'running')}}
    deltas = {'item/agentMessage/delta': 'text', 'item/reasoning/summaryTextDelta': 'reasoning',
              'item/commandExecution/outputDelta': 'tool', 'item/plan/delta': 'plan'}
    if method in deltas:
        return {**base, 'kind': 'delta', 'id': params['itemId'], 'type': deltas[method],
                'text': params.get('delta', ''), 'part': params.get('summaryIndex', 0)}
    if method in ('turn/started', 'turn/completed'):
        turn = params['turn']
        return {**base, 'kind': 'turn', 'turn': turn['id'], 'status': turn['status'],
                'error': (turn.get('error') or {}).get('message', '')}
    if method == 'thread/tokenUsage/updated':
        return {**base, 'kind': 'usage', 'usage': params['tokenUsage']}
    if method == 'error':
        return {**base, 'kind': 'error', 'error': params.get('error', {}).get('message', 'Codex error'),
                'retrying': params.get('willRetry', False)}
    return None
