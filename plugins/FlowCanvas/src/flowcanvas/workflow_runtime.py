"""Conversation-owned MCP processes; no worker model or hidden conversation."""
from jsonschema import Draft202012Validator
import asyncio
import os
import signal
import hashlib
from .subagents import execution_active
import json
import re
from pathlib import Path
import shutil
import socket
import sys
import time
import uuid
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from .workflow_tasks import WorkflowInput, attachment
from .plugin import get_plugin


def project_startup_stage(card):
    """Prepend the host lifecycle stage without changing UCAgent's stage indices."""
    startup = card.setdefault('startup', {'completed': card.get('status') == 'completed',
                                         'started_at': card.get('started_at')})
    stage = {'name': '启动工作流', 'kind': 'startup', 'completed': startup['completed'],
             'description': '启动 UCAgent MCP，完成初始化并获取工具清单。',
             'time_cost': (f"{startup['ended_at'] - startup['started_at']:.1f} 秒"
                           if startup.get('ended_at') and startup.get('started_at') else None)}
    card['stages'] = [stage] + [row for row in card.get('stages', []) if row.get('kind') != 'startup']
    if not startup['completed']:
        card['current_stage'] = stage['name']


class WorkflowRuntime:
    """Keep cards durable while process handles remain owned by this server."""

    def __init__(self, service, session):
        self.service, self.session = service, session
        self.processes = {}
        self.lock = asyncio.Lock()
        self.running_clocks = {}
        self.stage_clocks = {}
        self.retiring = {}
        self.stop_locks = {}
        self.stopped_processes = set()

    def update_running_time(self):
        """Accumulate active intervals only; monotonic clocks never survive a restart."""
        now = time.monotonic()
        for card in self.session.get('workflow_runs', []):
            stages = card.setdefault('stage_running_seconds', {})
            previous_stage = self.stage_clocks.pop(card['id'], None)
            if previous_stage:
                name, timestamp = previous_stage
                stages[name] = stages.get(name, 0) + max(0, now - timestamp)
            for stage in card.get('stages', []):
                if stage.get('kind') == 'startup':
                    continue
                name = stage['name']
                if name not in stages:
                    parts = re.findall(r'(\d+(?:\.\d+)?)\s*(h|m|s|秒)', str(stage.get('time_cost') or ''))
                    stages[name] = sum(float(value) * {'h': 3600, 'm': 60, 's': 1, '秒': 1}[unit] for value, unit in parts)
                stage['time_cost'] = f"{int(stages[name])}s"
                if name == card.get('current_stage') and not stage.get('completed') and card.get('status') == 'running':
                    self.stage_clocks[card['id']] = (name, now)
            previous = self.running_clocks.pop(card['id'], None)
            if previous is not None:
                card['running_seconds'] = card.get('running_seconds', 0) + max(0, now - previous)
            if card.get('status') in {'starting', 'running'}:
                card.setdefault('running_seconds', 0)
                self.running_clocks[card['id']] = now

    def publish(self):
        """Persist cards independently of model replies."""
        self.update_running_time()
        self.service.changed(self.session)
        self.service.save(self.session)

    async def stop_host(self, card):
        """Terminate the owned process group, including shell/compiler descendants."""
        async with self.stop_locks.setdefault(card['id'], asyncio.Lock()):
            proc = self.processes.get(card['id'])
            if proc is None or proc in self.stopped_processes:
                return
            # The host is a session leader; its group remains ours even after it exits.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), 3)
            except TimeoutError:
                pass
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
            self.stopped_processes.add(proc)
            card['alive'] = False

    async def retire_completed(self, card):
        """Allow the final tool response to drain before closing an accepted host."""
        await asyncio.sleep(2)
        async with self.lock:
            await self.stop_host(card)
            self.publish()

    async def inspect(self):
        """Read authoritative host receipts and reconcile process/turn liveness."""
        cards = self.session.setdefault('workflow_runs', [])
        before = json.dumps(cards)
        self.update_running_time()
        for card in cards:
            root = Path(card['root'])
            proc = self.processes.get(card['id'])
            card['alive'] = bool(proc and proc.returncode is None)
            try:
                progress = json.loads((root / 'progress.json').read_text())
                card.update(stages=progress['stages'], current_stage=progress['current_stage'], tui=progress.get('tui'))
            except (OSError, ValueError, KeyError):
                pass
            project_startup_stage(card)
            if (root / 'complete.json').is_file():
                card.update(status='completed', pause_reason='')
            elif card['status'] != 'paused' and (not card['alive'] or not execution_active(self.session)):
                card.update(status='paused', pause_reason='MCP 服务停止' if not card['alive'] else '对话已停止')
            if card['status'] == 'paused':
                await self.stop_host(card)
            elif card['status'] == 'completed' and card['id'] not in self.retiring:
                self.retiring[card['id']] = asyncio.create_task(self.retire_completed(card))
            if card['status'] == 'completed' or not card['alive']:
                for tool in (card.get('tui') or {}).get('tools', []):
                    tool['busy'] = False
        self.update_running_time()
        if before != json.dumps(cards):
            self.publish()
        return {'cards': cards}

    async def pause(self):
        """Stop workflow execution while retaining durable stage checkpoints."""
        for card in self.session.get('workflow_runs', []):
            if card['status'] != 'completed':
                card.update(status='paused', pause_reason='对话已停止')
            await self.stop_host(card)
        self.publish()

    async def run(self, workflow_id='', rtl_file='', dut='', card_id=''):
        """Only start or resume a process and return its execution contract."""
        return await self._dispatch(workflow_id, rtl_file, dut, card_id, '', {})

    async def invoke(self, card_id, tool_name, arguments):
        """Execute a named tool without creating or restarting an MCP process."""
        if not tool_name.strip():
            raise ValueError('Provide a tool_name from the run_workflow tool catalog')
        if not card_id:
            raise ValueError('Start the workflow first and provide its card_id')
        return await self._dispatch('', '', 'RTL', card_id, tool_name, arguments)

    async def _dispatch(self, workflow_id, rtl_file, dut, card_id, tool_name, arguments):
        """Validate availability and ownership; proxy only host-advertised tools."""
        async with self.lock:
            cards = self.session.setdefault('workflow_runs', [])
            card = next((c for c in cards if c['id'] == card_id), None) if card_id else None
            if card_id and card is None:
                raise ValueError('Card does not belong to this conversation')
            identity = card['workflow_id'] if card else workflow_id
            if self.session.get('permission_mode') not in {'workspace', 'full'}:
                raise ValueError('Select a supported access mode before running a workflow')
            # Availability applies equally to new starts, resumed cards and tool calls.
            attachment(WorkflowInput(workflow_id=identity))
            await self.inspect()
            if card and card['status'] == 'completed' and tool_name == 'Exit':
                return {'card': card, 'result': {'content': [{'type': 'text', 'text': '{"exit": true, "reason": "Workflow already accepted; MCP closes automatically."}'}], 'isError': False}}
            if card and card['status'] == 'completed' and not tool_name:
                return {'card': card, 'accepted': True}
            if card is None:
                project_root = None
                if identity in {'rtl-summary', 'picker-compile', 'plan-goals'}:
                    if not self.session.get('project_root'):
                        raise ValueError('Open a FlowCanvas project before running this RTL workflow')
                    project_root = Path(self.session['project_root']).resolve()
                    project = await asyncio.to_thread(self.service.projects.inspect, project_root)
                    rtl = project.get('rtl') or {}
                    if not rtl_file:
                        if not rtl.get('top_file') or not rtl.get('top'):
                            raise ValueError('Project top is unknown; provide rtl_file and dut')
                        rtl_file, dut = str(project_root / rtl['top_file']), rtl['top']
                    else:
                        source = Path(rtl_file).expanduser()
                        rtl_file = str(source if source.is_absolute() else project_root / source)
                        if not dut:
                            if rtl.get('top_file') and Path(rtl_file).resolve() == (project_root / rtl['top_file']).resolve():
                                dut = rtl.get('top', '')
                            if not dut:
                                raise ValueError('Provide dut for the explicitly selected RTL file')
                spec = attachment(WorkflowInput(workflow_id=identity, rtl_file=rtl_file, dut=dut or 'RTL'))
                if not spec['rtl_file']:
                    raise ValueError('Provide an existing RTL path')
                dut = spec['dut']
                root = self.service.root / 'workflow_runs' / uuid.uuid4().hex
                report = None
                if project_root and identity == 'rtl-summary':
                    report = project_root / 'output' / 'rtl-summary' / dut / root.name / 'summary.md'
                    if not report.resolve().is_relative_to(project_root):
                        raise ValueError('Summary output must remain inside the bound project')
                relative = Path(dut) / Path(spec['rtl_file']).name
                (root / 'workspace' / relative.parent).mkdir(parents=True)
                shutil.copyfile(spec['rtl_file'], root / 'workspace' / relative)
                config = next(w.config_file for w in get_plugin().workflows if w.name == identity)
                request = {'dut': dut, 'rtl': str(relative), 'config': str(config)}
                if identity == 'picker-compile':
                    output = project_root / 'verification' / dut
                    if not output.resolve().is_relative_to(project_root):
                        raise ValueError('DUT output must remain inside the bound project')
                    request.update(project_root=str(project_root), source_file=spec['rtl_file'], dut_output=str(output),
                                   source_hash=hashlib.sha256(Path(spec['rtl_file']).read_bytes()).hexdigest(),
                                   build_started_at=time.time())
                if identity == 'plan-goals':
                    goal_output = project_root / 'verification' / 'coverage' / dut
                    if not goal_output.resolve().is_relative_to(project_root):
                        raise ValueError('Coverage output must remain inside the project')
                    from .goal_planning import integration_snapshot
                    request.update(project_root=str(project_root), source_file=spec['rtl_file'],
                                   goal_output=str(goal_output),
                                   goal_baseline=integration_snapshot(project_root, goal_output, spec['rtl_file']))
                if report:
                    request.update(project_root=str(project_root), report_file=str(report))
                (root / 'request.json').write_text(json.dumps(request))
                card = {'id': root.name, 'conversation_id': self.session['id'], 'workflow_id': identity,
                        'name': spec['name'], 'root': str(root), 'workspace': str(root / 'workspace'),
                        'status': 'starting', 'stages': [], 'current_stage': '', 'started_at': time.time()}
                if report:
                    card.update(project_root=str(project_root), source_file=spec['rtl_file'],
                                top_module=dut, report_file=str(report))
                if identity == 'picker-compile':
                    card.update(project_root=str(project_root), source_file=spec['rtl_file'], top_module=dut, dut_output=str(output))
            root = Path(card['root'])
            proc = self.processes.get(card['id'])
            if not proc or proc.returncode is not None:
                if tool_name:
                    raise ValueError('MCP is stopped. Resume it with run_workflow(card_id) first.')
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
                spec = json.loads((root / 'request.json').read_text()); spec['port'] = port
                (root / 'request.json').write_text(json.dumps(spec))
                (root / 'ready.json').unlink(missing_ok=True)
                with (root / 'host.log').open('ab') as log:
                    proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'flowcanvas.workflow_host', str(root), stdout=log, stderr=log, start_new_session=True)
                self.processes[card['id']] = proc
                card.update(pid=proc.pid, mcp_instance=uuid.uuid4().hex, url=f'http://127.0.0.1:{port}/mcp', alive=True, status='starting')
                card['startup'] = {'completed': False, 'started_at': time.time()}
                project_startup_stage(card)
                if card not in cards:
                    cards.append(card)  # Created only after the MCP host process starts.
                self.publish()
                for _ in range(150):
                    if (root / 'ready.json').exists():
                        break
                    if proc.returncode is not None:
                        card.update(status='paused', alive=False, pause_reason='MCP 初始化失败，请查看 host.log')
                        self.publish()
                        return {'card': card}
                    await asyncio.sleep(.2)
                else:
                    card.update(status='paused', pause_reason='MCP 启动超时'); self.publish()
                    return {'card': card}
            if not execution_active(self.session):
                await self.pause()
                return {'card': card}
            if card['status'] != 'completed':
                card.update(status='running', pause_reason='')
            self.publish()
            # This endpoint is an owned loopback host, never a remote proxy target.
            async with (httpx2.AsyncClient(trust_env=False, timeout=httpx2.Timeout(30, read=1900 if identity == 'picker-compile' else 300)) as http,
                        streamable_http_client(card['url'], http_client=http) as streams):
                read, write = streams[:2]
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    catalog = await client.list_tools()
                    if not card.get('startup', {}).get('completed'):
                        card['startup'].update(completed=True, ended_at=time.time())
                        project_startup_stage(card)
                        self.publish()
                    result = None
                    if tool_name:
                        tool = next((t for t in catalog.tools if t.name == tool_name), None)
                        if tool is None:
                            raise ValueError('Tool is not exposed by this workflow. Use the tools returned by run_workflow.')
                        errors = list(Draft202012Validator(tool.input_schema).iter_errors(arguments))
                        if errors:
                            details = '; '.join(
                                f"{'.'.join(map(str, error.absolute_path)) or 'arguments'}: {error.message}"
                                for error in errors[:5])
                            raise ValueError(f'Invalid arguments for {tool_name}: {details}')
                        result = (await client.call_tool(tool_name, arguments, read_timeout_seconds=1900 if identity == 'picker-compile' else 300)).model_dump(mode='json')
                    await self.inspect()
                    if tool_name:
                        return {'card': card, 'result': result}
                    return {'card': card, 'instruction': json.loads((root / 'ready.json').read_text())['instruction'],
                            'tools': [t.model_dump(mode='json') for t in catalog.tools], 'result': result,
                            'next': 'Execute these instructions yourself. Use call_workflow_tool(card_id, tool_name, arguments) with this card ID and the returned tool schemas. Do not call stage tools directly or reconnect to load them. run_workflow is only for lifecycle operations. Completion requires host acceptance.'}

    async def close(self):
        """Stop all owned process groups and retirement tasks without deleting records."""
        for job in self.retiring.values():
            job.cancel()
        await asyncio.gather(*self.retiring.values(), return_exceptions=True)
        for card in self.session.get('workflow_runs', []):
            await self.stop_host(card)
        await self.inspect()
        for job in self.retiring.values():
            job.cancel()
        await asyncio.gather(*self.retiring.values(), return_exceptions=True)
