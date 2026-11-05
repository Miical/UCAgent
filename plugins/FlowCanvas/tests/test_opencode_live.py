"""Opt-in real-provider checks; all file writes stay inside a temporary workspace."""
import ast
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from fastapi import FastAPI
import httpx

from flowcanvas.backends.opencode import OpenCodeBackend
from flowcanvas.chat import ChatService, routes
from flowcanvas.workflow_bridge import WorkflowBridge


@unittest.skipUnless(os.environ.get('FLOWCANVAS_TEST_OPENCODE') == '1', 'requires a configured local OpenCode provider')
class OpenCodeLiveTests(unittest.IsolatedAsyncioTestCase):
    """Verify real native history, tools, policies, MCP, streaming and cancellation."""

    async def asyncSetUp(self):
        """Avoid asyncio debug repr logging of native responses containing provider config."""
        asyncio.get_running_loop().set_debug(False)

    async def test_project_model_catalog(self):
        """Read project model defaults through the API without invoking a provider model."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-config-') as directory:
            root = Path(directory)
            first = root / 'first'; first.mkdir()
            second = root / 'second'; second.mkdir()
            service = ChatService(root / 'state')
            service.backends['codex'].models = AsyncMock(return_value=[])
            app = FastAPI(); app.include_router(routes(service))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            try:
                models = await service.backends['opencode'].models()
                global_default = next((m['id'] for m in models if m['default']), models[0]['id'])
                alternate = next((m['id'] for m in models if m['id'] != global_default), None)
                if not alternate: self.skipTest('Need two configured models to verify different defaults')
                (first / 'opencode.json').write_text(json.dumps({'model': alternate}))
                service.projects.state['current'] = {'path': str(first)}
                owner = service.create('opencode')
                service.projects.state['current'] = {'path': str(second)}
                for params, expected in [({'session_id': owner['id']}, alternate), ({}, global_default)]:
                    response = await client.get('/api/chat/backends', params=params)
                    self.assertEqual(response.status_code, 200)
                    row = next(r for r in response.json() if r['id'] == 'opencode')
                    self.assertFalse(row.get('error'), row.get('error'))
                    self.assertEqual([m['id'] for m in row['models'] if m['default']], [expected])
                (first / 'opencode.json').write_text(json.dumps({'model': global_default}))
                refreshed = await client.get('/api/chat/backends', params={'session_id': owner['id']})
                row = next(r for r in refreshed.json() if r['id'] == 'opencode')
                self.assertEqual([m['id'] for m in row['models'] if m['default']], [global_default])
                self.assertEqual(service.backends['opencode'].workspace, service.workspace)
                print('project-scoped model catalogs and native defaults: passed', flush=True)
            finally:
                await client.aclose(); await service.close()

    async def test_native_roundtrip(self):
        """Exercise the same session across mode changes and native server restarts."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-live-') as directory:
            root = Path(directory)
            service = ChatService(root / 'state')
            owner = service.create('opencode')
            owner['permission_mode'] = 'full'
            bridge = WorkflowBridge(service, owner)
            await bridge.start()
            events, threads = [], []
            terminal = asyncio.Event()

            def emit(event):
                """Retain normalized events and wake only on a terminal native state."""
                events.append(event)
                if event['kind'] == 'turn' and event['status'] != 'inProgress':
                    print('terminal:', event['status'], event.get('error', ''), flush=True)
                    terminal.set()

            backend = OpenCodeBackend(root, emit)
            backend.mcp_url = bridge.url
            backend.workflow_instructions = 'You are a test assistant. Follow the user request exactly. Use the ucagent_list_workflow_runs MCP tool when requested.'
            try:
                catalog = await backend.models()
                model = next((row['id'] for row in catalog if row['default']), catalog[0]['id'])
                effort = 'low' if 'low' in next(row for row in catalog if row['id'] == model)['efforts'] else 'default'
                thread = await backend.open(None, model, 'full'); threads.append(thread)
                print('model:', model, flush=True)

                async def turn(text, mode='full'):
                    """Await actual termination and return only this turn's latest text blocks."""
                    terminal.clear(); events.clear()
                    await backend.send(thread, text, model, effort, mode=mode)
                    await asyncio.wait_for(terminal.wait(), 120)
                    self.assertEqual([e for e in events if e['kind'] == 'turn'][-1]['status'], 'completed')
                    latest = {}
                    for event in events:
                        if event['kind'] == 'item':
                            latest[event['item']['id']] = event['item']
                    return latest

                blocks = await turn('Remember marker FC_HISTORY_471. Create probe.txt containing exactly FC_TOOL_471 using a file writing tool. Then read it back with a tool. Reply with its contents.')
                self.assertEqual((root / 'probe.txt').read_text().strip(), 'FC_TOOL_471')
                self.assertTrue(any(x['kind'] == 'tool' and x['status'] == 'completed' for x in blocks.values()))
                self.assertTrue(any(e['kind'] == 'delta' for e in events))
                print('full tools and streaming: passed', flush=True)

                await backend.close()
                await backend.open(thread, model, 'full')
                blocks = await turn('What marker did I tell you to remember? Read probe.txt and report both values. Do not modify files.', 'full')
                texts = '\n'.join(x['text'] for x in blocks.values() if x['kind'] == 'text')
                self.assertIn('FC_HISTORY_471', texts)
                self.assertIn('FC_TOOL_471', texts)
                print('history resume and read: passed', flush=True)

                blocks = await turn('Call the ucagent_list_workflow_runs tool once and tell me how many workflow cards exist. Do not use shell commands.', 'full')
                self.assertTrue(any('list_workflow_runs' in x['title'] and x['status'] == 'completed' for x in blocks.values()))
                print('FlowCanvas MCP: passed', flush=True)

                @bridge.mcp.tool()
                async def flowcanvas_probe() -> dict:
                    """Return the deterministic live-test marker."""
                    return {'marker': 'FC_DYNAMIC_471'}

                await backend.reload_tools()
                blocks = await turn('Call ucagent_flowcanvas_probe and report its marker. Do not guess or use shell commands.')
                self.assertTrue(any('flowcanvas_probe' in x['title'] and x['status'] == 'completed' for x in blocks.values()))
                self.assertTrue(any('FC_DYNAMIC_471' in x['text'] for x in blocks.values()))
                print('dynamic MCP catalog: passed', flush=True)

                terminal.clear(); events.clear()
                turn_id = await backend.send(thread, 'Run sleep 4 in the shell, then reply ORIGINAL_ANSWER.', model, effort, mode='full')
                async with asyncio.timeout(120):
                    while not any(e['kind'] == 'item' and e['item']['kind'] == 'tool' and e['item']['status'] == 'running' for e in events):
                        if terminal.is_set(): self.fail('Turn finished before guidance boundary')
                        await asyncio.sleep(.1)
                await backend.steer(thread, turn_id, 'Change the final answer to GUIDED_471 instead. Do not repeat the sleep command.')
                await asyncio.wait_for(terminal.wait(), 120)
                texts = '\n'.join(e['item']['text'] for e in events if e['kind'] == 'item' and e['item']['kind'] == 'text')
                self.assertIn('GUIDED_471', texts)
                print('in-flight guidance: passed', flush=True)

                child = (await backend.http('POST', '/session', {'parentID': thread, 'title': 'Fixture child'}))['id']
                threads.append(child)
                await backend.http('POST', '/session/' + child + '/message', {'noReply': True, 'parts': [{'type': 'text', 'text': 'Child history fixture'}]})
                child_view = await backend.agents(thread, child)
                self.assertEqual(child_view['selected'], child)
                self.assertEqual(child_view['items'][0]['text'], 'Child history fixture')
                self.assertEqual(child_view['agents'][0]['status'], 'idle')
                unauthorized = await backend.agents(thread, 'ses_not_a_child')
                self.assertEqual(unauthorized['selected'], child)
                print('native child history and membership: passed', flush=True)

                terminal.clear(); events.clear()
                turn_id = await backend.send(thread, 'Run the shell command sleep 30, then reply DONE. Do not finish before the command returns.', model, effort, mode='full')
                async with asyncio.timeout(120):
                    while not any(e['kind'] == 'item' and e['item']['kind'] == 'tool' and e['item']['status'] == 'running' for e in events):
                        if terminal.is_set(): self.fail('Turn finished before cancellation boundary')
                        await asyncio.sleep(.1)
                await backend.interrupt(thread, turn_id)
                await asyncio.wait_for(terminal.wait(), 10)
                self.assertEqual([e for e in events if e['kind'] == 'turn'][-1]['status'], 'interrupted')
                print('native tool cancellation: passed', flush=True)
            finally:
                if threads:
                    await backend.connect()
                for thread in threads:
                    try:
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                    except RuntimeError:
                        pass
                await backend.close()
                await bridge.close()
                await service.close()

    async def test_sidebar_poll_cannot_reopen_retired_process(self):
        """Read fixture child history through a reconnect without creating an orphan server."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-observer-') as directory:
            service = ChatService(directory)
            owner = service.create('opencode')
            app = FastAPI(); app.include_router(routes(service))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            backend = OpenCodeBackend(service.workspace, lambda event: None)
            endpoint = '/api/chat/sessions/' + owner['id']
            entered, release = asyncio.Event(), asyncio.Event()
            threads, tasks = [], []
            original_close = backend.close

            async def close():
                """Hold ownership after the actual native process has already exited."""
                await original_close()
                entered.set(); await release.wait()

            try:
                native = await backend.open(None, '', 'full'); threads.append(native)
                child = (await backend.http('POST', '/session', {'parentID': native, 'title': 'Observer fixture'}))['id']
                threads.append(child)
                await backend.http('POST', '/session/' + child + '/message', {
                    'noReply': True, 'parts': [{'type': 'text', 'text': 'Observer history fixture'}]})
                owner.update(thread=native, status='completed', permission_mode='full')
                service.session_backends[owner['id']] = backend
                process = backend.process
                with patch.object(backend, 'close', side_effect=close):
                    changing = asyncio.create_task(client.post(endpoint + '/reconnect')); tasks.append(changing)
                    await asyncio.wait_for(entered.wait(), 10)
                    self.assertIsNotNone(process.returncode)
                    with patch.object(backend, 'connect', wraps=backend.connect) as connect:
                        poll = asyncio.create_task(client.get(endpoint + '/subagents')); tasks.append(poll)
                        with self.assertRaises(TimeoutError):
                            await asyncio.wait_for(asyncio.shield(poll), .1)
                        connect.assert_not_awaited()
                        release.set()
                        self.assertEqual((await changing).status_code, 200)
                        result = await asyncio.wait_for(poll, 20)
                        self.assertEqual(result.status_code, 200)
                        self.assertEqual(result.json()['selected'], child)
                        self.assertEqual(result.json()['items'][0]['text'], 'Observer history fixture')
                        connect.assert_not_awaited()
                self.assertIs(backend.process, process)
                self.assertIsNotNone(backend.process.returncode)
                self.assertNotIn(owner['id'], service.session_backends)
                self.assertIsNone(service.backends['opencode'].process.returncode)
                print('native observer waits, retired process stays stopped, child history retained: passed', flush=True)
            finally:
                release.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                cleanup = service.backends['opencode']
                try:
                    if threads:
                        await cleanup.connect()
                        for identity in reversed(threads):
                            await cleanup.http('DELETE', '/session/' + identity)
                finally:
                    await original_close()
                    await client.aclose(); await service.close()

    async def test_lost_prompt_and_guidance_ack_keep_native_execution(self):
        """Recover real accepted user IDs after injected acknowledgment loss, without resending."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-ack-') as directory:
            events, posted = [], []
            terminal = asyncio.Event()

            def emit(event):
                """Observe tools and terminal state from the real native event stream."""
                events.append(event)
                if event['kind'] == 'turn' and event['status'] != 'inProgress': terminal.set()

            backend = OpenCodeBackend(directory, emit)
            thread = None
            original_http = backend.http

            async def lose_ack(method, path, body=None, **kwargs):
                """Lose only the acknowledgment after the native server accepted the request."""
                result = await original_http(method, path, body, **kwargs)
                if method == 'POST' and path.endswith('/prompt_async'):
                    posted.append(body['messageID'])
                    raise RuntimeError('Injected acknowledgment loss after native acceptance')
                return result

            try:
                models = await backend.models()
                model = next((row for row in models if row['default']), models[0])
                effort = 'low' if 'low' in model['efforts'] else 'default'
                thread = await backend.open(None, model['id'], 'full')
                backend.http = lose_ack
                turn = await backend.send(thread,
                    'Run sleep 4 in the shell, then reply exactly ORIGINAL_739. Do not modify files or delegate.',
                    model['id'], effort, mode='full')
                async with asyncio.timeout(120):
                    while not any(e['kind'] == 'item' and e['item']['kind'] == 'tool'
                                  and e['item']['status'] == 'running' for e in events):
                        self.assertFalse(terminal.is_set(), 'Native turn ended before the guidance boundary')
                        await asyncio.sleep(.1)
                await backend.steer(thread, turn, 'Change the final answer to GUIDED_739. Do not repeat the sleep command.')
                await asyncio.wait_for(terminal.wait(), 120)
                self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'], ['inProgress', 'completed'])
                self.assertEqual(len(posted), 2)
                self.assertEqual(len(set(posted)), 2)
                records = await original_http('GET', '/session/' + thread + '/message')
                users = [row['info']['id'] for row in records if row['info']['role'] == 'user']
                self.assertEqual(set(users), set(posted))
                self.assertEqual(len(users), 2)
                final = '\n'.join(p.get('text', '') for row in records if row['info']['role'] == 'assistant'
                                  for p in row['parts'] if p['type'] == 'text')
                self.assertIn('GUIDED_739', final)
                commands = [p for row in records for p in row['parts'] if p['type'] == 'tool'
                            and p.get('tool') == 'bash']
                self.assertEqual(len(commands), 1)
                self.assertEqual(commands[0]['state']['status'], 'completed')
                print('real accepted prompt and guidance recovered; one command and no resent messages: passed', flush=True)
            finally:
                backend.http = original_http
                try:
                    if thread:
                        await backend.connect()
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                finally:
                    await backend.close()

    async def test_guidance_crosses_native_idle_without_losing_its_reply(self):
        """Keep one FlowCanvas task while delayed guidance starts a second native response."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-guidance-') as directory:
            events = []
            terminal, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

            def emit(event):
                """Observe whether the projected task ends before its delayed input is answered."""
                events.append(event)
                if event['kind'] == 'turn' and event['status'] != 'inProgress': terminal.set()

            backend = OpenCodeBackend(directory, emit)
            thread = steering = None
            original_http = backend.http

            async def delay_guidance(method, path, body=None, **kwargs):
                """Wait before dispatching guidance until the earlier native reply is complete."""
                if method == 'POST' and path.endswith('/prompt_async'):
                    entered.set(); await release.wait()
                return await original_http(method, path, body, **kwargs)

            try:
                models = await backend.models()
                model = next((row for row in models if row['default']), models[0])
                effort = 'low' if 'low' in model['efforts'] else 'default'
                thread = await backend.open(None, model['id'], 'full')
                turn = await backend.send(thread,
                    'Reply FIRST_840 followed by twelve numbered one-sentence descriptions of verification checks. '
                    'Do not use tools, modify files or delegate.', model['id'], effort, mode='full')
                backend.http = delay_guidance
                steering = asyncio.create_task(backend.steer(thread, turn,
                    'Now reply SECOND_840 followed by the marker from my previous request. Do not use tools or delegate.'))
                await asyncio.wait_for(entered.wait(), 5)
                async with asyncio.timeout(120):
                    while True:
                        records = await original_http('GET', '/session/' + thread + '/message')
                        state = await original_http('GET', '/session/status')
                        if (state.get(thread, {}).get('type', 'idle') == 'idle'
                                and any(r['info']['role'] == 'assistant' and r['info'].get('time', {}).get('completed') for r in records)):
                            break
                        await asyncio.sleep(.1)
                await backend.settle(thread)
                self.assertEqual(backend.active.get(thread), turn)
                self.assertFalse(terminal.is_set())
                release.set(); await asyncio.wait_for(steering, 20)
                await asyncio.wait_for(terminal.wait(), 120)
                self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'], ['inProgress', 'completed'])
                texts = '\n'.join(e['item']['text'] for e in events if e['kind'] == 'item' and e['item']['kind'] == 'text')
                self.assertIn('FIRST_840', texts)
                self.assertIn('SECOND_840', texts)
                records = await original_http('GET', '/session/' + thread + '/message')
                self.assertEqual(sum(r['info']['role'] == 'user' for r in records), 2)
                print('delayed guidance survives real native idle; both replies remain in one projected task: passed', flush=True)
            finally:
                if steering and not steering.done(): steering.cancel()
                if steering: await asyncio.gather(steering, return_exceptions=True)
                backend.http = original_http
                try:
                    if thread:
                        await backend.connect()
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                finally:
                    await backend.close()

    async def test_stop_during_guidance_delivery(self):
        """A real native session stays idle after stopping a delayed guidance submission."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-stop-guidance-') as directory:
            events = []
            entered, release = asyncio.Event(), asyncio.Event()
            backend = OpenCodeBackend(directory, events.append)
            original_http = backend.http
            thread = steering = stopping = None

            async def delay_guidance(method, path, body=None, **kwargs):
                """Hold delivery before dispatch so stop can cross the request in flight."""
                if method == 'POST' and path.endswith('/prompt_async'):
                    entered.set(); await release.wait()
                return await original_http(method, path, body, **kwargs)

            try:
                models = await backend.models()
                model = next((row for row in models if row['default']), models[0])
                effort = 'low' if 'low' in model['efforts'] else 'default'
                thread = await backend.open(None, model['id'], 'full')
                turn = await backend.send(thread,
                    'Write 100 numbered one-sentence descriptions of verification checks. '
                    'Do not use tools, modify files or delegate.', model['id'], effort, mode='full')
                async with asyncio.timeout(30):
                    while (await original_http('GET', '/session/status')).get(thread, {}).get('type') != 'busy':
                        await asyncio.sleep(.05)
                backend.http = delay_guidance
                steering = asyncio.create_task(backend.steer(thread, turn,
                    'Instead write 100 numbered one-sentence descriptions of cache checks. Do not use tools or delegate.'))
                await asyncio.wait_for(entered.wait(), 5)
                native_process = backend.process
                stopping = asyncio.create_task(backend.interrupt(thread, turn))
                await asyncio.sleep(.05)
                release.set()
                results = await asyncio.wait_for(asyncio.gather(steering, stopping, return_exceptions=True), 30)
                self.assertNotIsInstance(results[1], BaseException)
                self.assertIsNotNone(native_process.returncode)
                await backend.connect()
                self.assertNotEqual(backend.process.pid, native_process.pid)
                # Reopen the same history only after the old execution process exits.
                for _ in range(10):
                    await asyncio.sleep(.1)
                    states = await original_http('GET', '/session/status')
                    self.assertEqual(states.get(thread, {}).get('type', 'idle'), 'idle')
                self.assertNotIn(thread, backend.active)
                self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'], ['inProgress', 'interrupted'])
                print('stopping delayed guidance leaves the actual native session idle: passed', flush=True)
            finally:
                for task in (steering, stopping):
                    if task and not task.done(): task.cancel()
                await asyncio.gather(*(task for task in (steering, stopping) if task), return_exceptions=True)
                backend.http = original_http
                try:
                    if thread:
                        await backend.connect()
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                finally:
                    await backend.close()

    async def test_stop_during_native_preparation_prevents_late_execution(self):
        """Stop an acknowledged prompt while native preparation has not entered its execution loop."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-native-preparation-') as directory:
            entered, release, model_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
            service = ChatService(Path(directory) / 'state')
            owner = service.create('opencode')
            bridge = WorkflowBridge(service, owner)

            @bridge.mcp.resource('fixture://pending-input', mime_type='text/plain')
            async def pending_input() -> str:
                """Hold only this fixture's native message preparation before persistence."""
                entered.set()
                await release.wait()
                return 'Owned preparation fixture.'

            async def provider(request):
                """Record model execution and serve the explicitly requested recovery turn."""
                await request.read()
                model_started.set()
                chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture',
                         'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'PREPARATION_RECOVERED'},
                                      'finish_reason': None}]}
                first = 'data: ' + json.dumps(chunk) + '\n\n'
                chunk['choices'][0].update(delta={}, finish_reason='stop')
                return web.Response(content_type='text/event-stream',
                    text=first + 'data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')

            provider_app = web.Application(); provider_app.router.add_post('/v1/chat/completions', provider)
            runner = web.AppRunner(provider_app); await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
            port = site._server.sockets[0].getsockname()[1]
            Path(directory, 'opencode.json').write_text(json.dumps({'provider': {'flowcanvas-fixture': {
                'npm': '@ai-sdk/openai-compatible', 'name': 'FlowCanvas fixture',
                'options': {'baseURL': f'http://127.0.0.1:{port}/v1', 'apiKey': 'nonsecret-test-fixture'},
                'models': {'fixture': {'name': 'Fixture', 'limit': {'context': 65536, 'output': 200}}}}}}))
            events = []
            backend = OpenCodeBackend(directory, events.append)
            original_http = backend.http
            thread = None

            async def add_preparation_barrier(method, path, body=None, **kwargs):
                """Add a temporary resource part solely to expose the native preparation boundary."""
                if method == 'POST' and path.endswith('/prompt_async'):
                    body = {**body, 'parts': [*body['parts'], {'type': 'file', 'mime': 'text/plain',
                        'url': 'fixture://pending-input', 'filename': 'pending-input',
                        'source': {'type': 'resource', 'clientName': 'ucagent', 'uri': 'fixture://pending-input',
                                   'text': {'value': 'pending-input', 'start': 0, 'end': 13}}}]}
                return await original_http(method, path, body, **kwargs)

            try:
                await bridge.start()
                backend.mcp_url = bridge.url
                thread = await backend.open(None, 'flowcanvas-fixture/fixture', 'full')
                native_process, native_readers = backend.process, set(backend.tasks)
                backend.http = add_preparation_barrier
                turn = await backend.send(thread, 'Reply without tools.', 'flowcanvas-fixture/fixture', 'default', mode='full')
                await asyncio.wait_for(entered.wait(), 15)
                states = await original_http('GET', '/session/status')
                self.assertEqual(states.get(thread, {}).get('type', 'idle'), 'idle')
                records = await original_http('GET', '/session/' + thread + '/message')
                self.assertFalse(any(r['info']['id'] == turn for r in records))
                self.assertFalse(model_started.is_set())
                await asyncio.wait_for(backend.interrupt(thread, turn), 20)
                self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'], ['inProgress', 'interrupted'])
                self.assertIsNotNone(native_process.returncode)
                self.assertTrue(all(task.done() for task in native_readers))
                release.set()
                try:
                    await asyncio.wait_for(model_started.wait(), 2)
                except TimeoutError:
                    pass
                self.assertFalse(model_started.is_set(), 'Native preparation resumed model work after stop had returned')
                backend.http = original_http
                await backend.connect()
                self.assertNotEqual(backend.process.pid, native_process.pid)
                states = await original_http('GET', '/session/status')
                self.assertEqual(states.get(thread, {}).get('type', 'idle'), 'idle')
                await backend.send(thread, 'New request: reply without tools.', 'flowcanvas-fixture/fixture', 'default', mode='full')
                async with asyncio.timeout(10):
                    while thread in backend.active:
                        await asyncio.sleep(.01)
                self.assertTrue(model_started.is_set())
                self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'],
                                 ['inProgress', 'interrupted', 'inProgress', 'completed'])
                print('acknowledged native preparation canceled; same-session next turn completed: passed', flush=True)
            finally:
                release.set()
                backend.http = original_http
                try:
                    if thread:
                        await backend.connect()
                        await original_http('POST', '/session/' + thread + '/abort', {})
                        await original_http('DELETE', '/session/' + thread)
                finally:
                    await backend.close()
                    await bridge.close()
                    await service.close()
                    await runner.cleanup()

    async def test_provider_retry_recovers_without_duplicate_submission(self):
        """Native transient retries remain one task and one persisted user request."""
        await self.check_provider_retry('recover')

    async def test_provider_retry_can_be_stopped_without_late_restart(self):
        """Stopping native backoff prevents the delayed retry and preserves queued input."""
        await self.check_provider_retry('stop')

    async def test_provider_retry_exhaustion_preserves_failed_task_and_queue(self):
        """Exhausted native retries settle once as failure without launching queued work."""
        await self.check_provider_retry('exhaust')

    async def check_provider_retry(self, outcome):
        """Drive real native retries via public chat APIs and an isolated HTTP model service."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-retry-') as directory:
            service = ChatService(directory)
            service.flusher = asyncio.create_task(service.flush())
            calls = []
            streaming, release = asyncio.Event(), asyncio.Event()

            async def provider(request):
                """Return retryable errors, then optionally hold a valid incremental reply."""
                await request.read()
                calls.append(request.path)
                if outcome != 'recover' or len(calls) <= 2:
                    return web.json_response({'error': {'message': 'FC_RETRY_812 service unavailable',
                        'type': 'server_error', 'code': 'service_unavailable'}}, status=503,
                        headers={'retry-after-ms': '1200' if outcome == 'stop' else '150'})
                response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await response.prepare(request)
                chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture',
                         'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'RECOVERED_FC_RETRY_812'},
                                      'finish_reason': None}]}
                try:
                    await response.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                    streaming.set(); await release.wait()
                    chunk['choices'][0].update(delta={}, finish_reason='stop')
                    await response.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
                    await response.write_eof()
                except (ConnectionResetError, RuntimeError):
                    pass
                return response

            provider_app = web.Application(); provider_app.router.add_post('/v1/chat/completions', provider)
            runner = web.AppRunner(provider_app); await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
            port = site._server.sockets[0].getsockname()[1]
            (service.workspace / 'opencode.json').write_text(json.dumps({'provider': {'flowcanvas-fixture': {
                'npm': '@ai-sdk/openai-compatible', 'name': 'FlowCanvas fixture',
                'options': {'baseURL': f'http://127.0.0.1:{port}/v1', 'apiKey': 'nonsecret-test-fixture'},
                'models': {'fixture': {'name': 'Fixture', 'limit': {'context': 65536, 'output': 200}}}}}}))
            app = FastAPI(); app.include_router(routes(service))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            owner = None
            try:
                models = await service.backends['opencode'].models()
                service.catalogs[('opencode', str(service.workspace))] = models
                owner = service.get((await client.post('/api/chat/sessions', json={'backend': 'opencode'})).json()['id'])
                owner['permission_mode'] = 'full'
                endpoint = '/api/chat/sessions/' + owner['id']
                model = 'flowcanvas-fixture/fixture'
                sent = await client.post(endpoint + '/messages', json={
                    'text': 'Reply with one word and do not use tools.', 'model': model, 'effort': 'default',
                    'task_card': {'title': 'Retry check', 'workflow': '', 'project': 'temporary test'}})
                self.assertEqual(sent.status_code, 202)
                async with asyncio.timeout(20):
                    while 'FC_RETRY_812' not in owner.get('error', ''):
                        self.assertIn(owner['status'], {'starting', 'running'})
                        await asyncio.sleep(.01)
                self.assertEqual(owner['status'], 'running')
                self.assertIsNone(owner.get('ended_at'))
                backend = service.session_backends[owner['id']]
                self.assertIn(owner['thread'], backend.active)
                self.assertTrue(owner['items'][0]['task_card'])
                self.assertNotIn('task_run', owner['items'][0], 'Live cards must not acquire a terminal checkpoint')
                if outcome == 'recover':
                    await asyncio.wait_for(streaming.wait(), 10)
                    async with asyncio.timeout(5):
                        while not any('RECOVERED_FC_RETRY_812' in row['text'] for row in owner['items'] if row['kind'] == 'text'):
                            await asyncio.sleep(.01)
                    self.assertEqual(owner['status'], 'running')
                    print('retry notice remains during recovered streaming:', bool(owner.get('error')), flush=True)
                    release.set()
                else:
                    queued = await client.post(endpoint + '/messages', json={
                        'text': 'QUEUED_MUST_NOT_RUN', 'model': model, 'effort': 'default'})
                    self.assertEqual(queued.status_code, 202)
                    if outcome == 'stop':
                        self.assertEqual((await client.post(endpoint + '/stop')).status_code, 200)
                        stopped_calls = len(calls)
                        await asyncio.sleep(1.4)  # Exceed the fixture's native retry-after interval.
                        self.assertEqual(len(calls), stopped_calls)
                async with asyncio.timeout(20):
                    while owner['status'] in {'starting', 'running', 'stopping'}:
                        await asyncio.sleep(.01)
                expected = {'recover': 'completed', 'stop': 'interrupted', 'exhaust': 'error'}[outcome]
                self.assertEqual(owner['status'], expected, owner.get('error'))
                self.assertNotIn(owner['thread'], backend.active)
                await backend.connect()
                records = await backend.http('GET', '/session/' + owner['thread'] + '/message')
                self.assertEqual(sum(row['info']['role'] == 'user' for row in records), 1)
                states = await backend.http('GET', '/session/status')
                self.assertEqual(states.get(owner['thread'], {}).get('type', 'idle'), 'idle')
                saved = json.loads((service.root / (owner['id'] + '.json')).read_text())
                self.assertEqual(saved['status'], expected)
                self.assertEqual(saved['items'][0]['task_run']['status'], expected)
                if outcome == 'recover':
                    self.assertEqual(len(calls), 3)
                    self.assertFalse(saved['error'])
                else:
                    self.assertTrue(saved['queue_paused'])
                    self.assertEqual(len(saved['queue']), 1)
                    if outcome == 'exhaust':
                        self.assertEqual(len(calls), 6)
                        self.assertIn('FC_RETRY_812', saved['error'])
                print('native retry lifecycle:', json.dumps({'outcome': outcome, 'requests': len(calls),
                    'status': expected, 'native_users': 1, 'queued': len(saved.get('queue', []))}), flush=True)
            finally:
                release.set()
                try:
                    if owner and owner.get('thread'):
                        backend = service.session_backends[owner['id']]
                        await backend.connect()
                        await backend.http('POST', '/session/' + owner['thread'] + '/abort', {})
                        await backend.http('DELETE', '/session/' + owner['thread'])
                finally:
                    await client.aclose()
                    await service.close()
                    await runner.cleanup()

    async def test_catalog_refresh_preserves_pending_native_tools(self):
        """A catalog refresh must not close the client captured by an in-flight model step."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-tool-refresh-') as directory:
            events, calls = [], []
            waiting, release, terminal = asyncio.Event(), asyncio.Event(), asyncio.Event()

            async def provider(request):
                """Hold one actual model request before returning its chosen MCP invocation."""
                body = await request.json()
                calls.append({row['function']['name'] for row in body.get('tools', [])})
                first = len(calls) == 1
                if first:
                    waiting.set(); await release.wait()
                response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await response.prepare(request)
                delta = {'role': 'assistant', 'content': 'REFRESH_OK'}
                if first:
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'refresh_call',
                        'type': 'function', 'function': {'name': 'ucagent_list_workflow_runs', 'arguments': '{}'}}]}
                chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture',
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
                await response.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                chunk['choices'][0].update(delta={}, finish_reason='tool_calls' if first else 'stop')
                chunk['usage'] = {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}
                await response.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
                await response.write_eof()
                return response

            app = web.Application(); app.router.add_post('/v1/chat/completions', provider)
            runner = web.AppRunner(app); await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
            port = site._server.sockets[0].getsockname()[1]
            Path(directory, 'opencode.json').write_text(json.dumps({'compaction': {'auto': False},
                'provider': {'flowcanvas-fixture': {'npm': '@ai-sdk/openai-compatible', 'name': 'FlowCanvas fixture',
                    'options': {'baseURL': f'http://127.0.0.1:{port}/v1', 'apiKey': 'nonsecret-test-fixture'},
                    'models': {'fixture': {'name': 'Fixture', 'limit': {'context': 65536, 'output': 200}}}}}}))

            def emit(event):
                """Record tool completion separately from the assistant's claimed completion."""
                events.append(event)
                if event['kind'] == 'turn' and event['status'] != 'inProgress': terminal.set()

            service = ChatService(Path(directory) / 'state')
            owner = service.create('opencode')
            bridge = WorkflowBridge(service, owner)
            service.workflow_bridges[owner['id']] = bridge
            backend, thread = OpenCodeBackend(directory, emit), None
            try:
                await bridge.start()
                backend.mcp_url = bridge.url
                model = 'flowcanvas-fixture/fixture'
                thread = await backend.open(None, model, 'full')
                await backend.send(thread, 'List the workflow cards once.', model, 'default', mode='full')
                await asyncio.wait_for(waiting.wait(), 10)
                self.assertIn('ucagent_list_workflow_runs', calls[0])

                @bridge.mcp.tool()
                async def published_after_start() -> dict:
                    """Expose a new schema only after the first provider request has begun."""
                    return {'new_tool': True}

                await backend.reload_tools()
                release.set()
                await asyncio.wait_for(terminal.wait(), 20)
                records = await backend.http('GET', '/session/' + thread + '/message')
                tools = [part for record in records for part in record['parts'] if part['type'] == 'tool']
                self.assertEqual(len(tools), 1)
                print('native tool refresh:', json.dumps({'status': tools[0]['state']['status'],
                    'error': tools[0]['state'].get('error'), 'provider_calls': len(calls)}), flush=True)
                self.assertEqual(tools[0]['state']['status'], 'completed', tools[0]['state'].get('error'))
                self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'], ['inProgress', 'completed'])
                self.assertNotIn('ucagent_published_after_start', calls[0])
                await backend.reload_tools()
                events.clear(); terminal.clear()
                await backend.send(thread, 'Reply REFRESH_OK.', model, 'default', mode='full')
                await asyncio.wait_for(terminal.wait(), 20)
                self.assertEqual(len(calls), 3)
                self.assertIn('ucagent_published_after_start', calls[-1])
                self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'], ['inProgress', 'completed'])
            finally:
                release.set()
                try:
                    if thread:
                        await backend.connect()
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                finally:
                    await backend.close()
                    await service.close()
                    await runner.cleanup()

    async def test_native_usage_includes_cache_and_reasoning(self):
        """Normalize real CLI accounting and recover it when message metadata events are lost."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-usage-') as directory:
            events, calls = [], []
            terminal = asyncio.Event()

            async def provider(request):
                """Stream deterministic provider counters without contacting an external model."""
                await request.read()
                calls.append(request.path)
                response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await response.prepare(request)
                chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture',
                         'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'USAGE_OK'},
                                      'finish_reason': None}]}
                await response.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                chunk['choices'][0].update(delta={}, finish_reason='stop')
                chunk['usage'] = {'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130,
                                  'prompt_tokens_details': {'cached_tokens': 40},
                                  'completion_tokens_details': {'reasoning_tokens': 12}}
                await response.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
                await response.write_eof()
                return response

            app = web.Application(); app.router.add_post('/v1/chat/completions', provider)
            runner = web.AppRunner(app); await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
            port = site._server.sockets[0].getsockname()[1]
            Path(directory, 'opencode.json').write_text(json.dumps({'compaction': {'auto': False},
                'provider': {'flowcanvas-fixture': {'npm': '@ai-sdk/openai-compatible', 'name': 'FlowCanvas fixture',
                    'options': {'baseURL': f'http://127.0.0.1:{port}/v1', 'apiKey': 'nonsecret-test-fixture'},
                    'models': {'fixture': {'name': 'Fixture', 'limit': {'context': 65536, 'output': 200}}}}}}))

            def emit(event):
                """Retain normalized usage until this task reaches its native terminal state."""
                events.append(event)
                if event['kind'] == 'turn' and event['status'] != 'inProgress': terminal.set()

            backend = OpenCodeBackend(directory, emit)
            original_event, thread, drop_metadata = backend.event, None, False

            async def transport(event):
                """Drop only metadata notifications to require final native snapshot recovery."""
                if drop_metadata and event.get('type') == 'message.updated': return
                await original_event(event)

            backend.event = transport
            try:
                model = 'flowcanvas-fixture/fixture'
                thread = await backend.open(None, model, 'full')
                for drop_metadata in (False, True):
                    with self.subTest(metadata_events=not drop_metadata):
                        events.clear(); terminal.clear()
                        turn = await backend.send(thread, 'Reply USAGE_OK without tools.', model, 'default', mode='full')
                        await asyncio.wait_for(terminal.wait(), 20)
                        self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'], ['inProgress', 'completed'])
                        records = await backend.http('GET', '/session/' + thread + '/message')
                        native = [r['info'] for r in records if r['info'].get('parentID') == turn][-1]['tokens']
                        self.assertEqual(native, {'input': 60, 'output': 18, 'reasoning': 12,
                                                  'cache': {'read': 40, 'write': 0}, 'total': 130})
                        usage = [e['usage'] for e in events if e['kind'] == 'usage']
                        print('native usage:', json.dumps({'metadata_events': not drop_metadata,
                            'native': native, 'projected': usage[-1] if usage else None}), flush=True)
                        self.assertTrue(usage, 'Final native accounting was not recovered')
                        self.assertEqual(usage[-1]['last'], {'inputTokens': 100, 'outputTokens': 30,
                            'cachedInputTokens': 40, 'cacheWriteInputTokens': 0,
                            'reasoningOutputTokens': 12, 'totalTokens': 130})
                        self.assertNotIn('total', usage[-1], 'Latest model call is not a task total')
                self.assertEqual(len(calls), 2)
            finally:
                try:
                    if thread:
                        await backend.connect()
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                finally:
                    await backend.close()
                    await runner.cleanup()

    async def test_provider_overflow_recovers_through_native_compaction(self):
        """A provider context rejection can compact and resume the same projected task."""
        await self.check_provider_context_overflow(auto=True, fail_summary=False)

    async def test_provider_overflow_fails_with_compaction_disabled(self):
        """An explicitly disabled native recovery still produces a visible terminal failure."""
        await self.check_provider_context_overflow(auto=False, fail_summary=False)

    async def test_provider_overflow_during_compaction_remains_a_failure(self):
        """A rejected summary must not be mistaken for successful context recovery."""
        await self.check_provider_context_overflow(auto=True, fail_summary=True)

    async def check_provider_context_overflow(self, *, auto, fail_summary):
        """Use real OpenCode with a local provider to exercise its overflow state machine."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-overflow-') as directory:
            calls, events, overflow_signals = [], [], []
            terminal = asyncio.Event()

            async def provider(request):
                """Reject one normal request, optionally its summary, and otherwise stream fixture text."""
                await request.read()
                calls.append(request.path)
                number = len(calls)
                if number == 2 or fail_summary and number == 3:
                    return web.json_response({'error': {
                        'message': "This model's maximum context length is 16000 tokens. The request exceeds the context window.",
                        'type': 'invalid_request_error', 'code': 'context_length_exceeded'}}, status=400)
                content = ('READY_FC_CTX_709' if number == 1 else
                           'INTERNAL_FC_CTX_709: retain the follow-up request.' if number == 3 else 'FINAL_FC_CTX_709')
                response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await response.prepare(request)
                chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture',
                         'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': content}, 'finish_reason': None}]}
                try:
                    await response.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                    chunk['choices'][0].update(delta={}, finish_reason='stop')
                    chunk['usage'] = {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}
                    await response.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
                    await response.write_eof()
                except (ConnectionResetError, RuntimeError):
                    pass
                return response

            app = web.Application(); app.router.add_post('/v1/chat/completions', provider)
            runner = web.AppRunner(app); await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
            port = site._server.sockets[0].getsockname()[1]
            Path(directory, 'opencode.json').write_text(json.dumps({'compaction': {'auto': auto},
                'provider': {'flowcanvas-fixture': {'npm': '@ai-sdk/openai-compatible', 'name': 'FlowCanvas fixture',
                    'options': {'baseURL': f'http://127.0.0.1:{port}/v1', 'apiKey': 'nonsecret-test-fixture'},
                    'models': {'fixture': {'name': 'Fixture', 'limit': {'context': 65536, 'output': 200}}}}}}))

            def emit(event):
                """Record normalized task ownership without logging request or response bodies."""
                events.append(event)
                if event['kind'] == 'turn' and event['status'] != 'inProgress': terminal.set()

            backend = OpenCodeBackend(directory, emit)
            original_event = backend.event
            thread = None

            async def observe_native(event):
                """Observe genuine overflow notifications before the adapter projects them."""
                if event.get('type') == 'session.error':
                    overflow_signals.append(event.get('properties', {}).get('error', {}).get('name'))
                await original_event(event)

            try:
                backend.event = observe_native
                model = 'flowcanvas-fixture/fixture'
                thread = await backend.open(None, model, 'full')
                await backend.send(thread, 'Remember FC_CTX_709. Reply READY without tools.', model, 'default', mode='full')
                await asyncio.wait_for(terminal.wait(), 20)
                self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'], ['inProgress', 'completed'])
                events.clear(); terminal.clear()
                turn = await backend.send(thread, 'FOLLOWUP_FC_CTX_709: reply FINAL without tools.', model, 'default', mode='full')
                await asyncio.wait_for(terminal.wait(), 30)
                self.assertIn('ContextOverflowError', overflow_signals)
                expected = 'completed' if auto and not fail_summary else 'failed'
                turns = [e for e in events if e['kind'] == 'turn']
                self.assertEqual([e['status'] for e in turns], ['inProgress', expected], turns[-1].get('error'))
                self.assertEqual({e['turn'] for e in turns}, {turn})
                self.assertNotIn(thread, backend.active)
                await backend.connect()
                records = await backend.http('GET', '/session/' + thread + '/message')
                summaries = [r for r in records if r['info']['role'] == 'assistant' and r['info'].get('summary')]
                visible = '\n'.join(e['item']['text'] for e in events if e['kind'] == 'item') + '\n'.join(
                    e['text'] for e in events if e['kind'] == 'delta')
                self.assertNotIn('INTERNAL_FC_CTX_709', visible)
                if expected == 'completed':
                    self.assertIn('FINAL_FC_CTX_709', visible)
                    self.assertTrue(any(r['info'].get('finish') and not r['info'].get('error') for r in summaries))
                else:
                    self.assertTrue(turns[-1]['error'])
                    if fail_summary:
                        self.assertTrue(any(r['info'].get('error', {}).get('name') == 'ContextOverflowError' for r in summaries))
                    else:
                        self.assertFalse(summaries)
                print('native provider overflow:', json.dumps({'auto': auto, 'fail_summary': fail_summary,
                    'requests': len(calls), 'summaries': len(summaries), 'result': expected}), flush=True)
            finally:
                try:
                    if thread:
                        await backend.connect()
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                finally:
                    await backend.close()
                    await runner.cleanup()

    async def test_native_failure_during_guidance_delivery(self):
        """A real native provider failure cannot leave delayed guidance running unowned."""
        await self.check_native_failure_delivery(abort_fault=False)

    async def test_native_failure_retires_error_reader(self):
        """Retire a real native connection from its error reader and resume its saved history."""
        await self.check_native_failure_delivery(abort_fault=True)

    async def check_native_failure_delivery(self, *, abort_fault):
        """Exercise native provider failure, optional abort transport loss, and subsequent recovery."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-failure-guidance-') as directory:
            failed, release_failure = asyncio.Event(), asyncio.Event()
            entered, release_guidance, release_provider = asyncio.Event(), asyncio.Event(), asyncio.Event()
            terminal = asyncio.Event()
            calls = []

            async def provider(request):
                """Reject the first model request, then hold a valid local stream until cleanup."""
                await request.read()
                calls.append(request.path)
                if len(calls) == 1:
                    return web.json_response({'error': {'message': 'FC_LOCAL_REJECT_621',
                        'type': 'invalid_request_error', 'code': 'invalid_request'}}, status=400)
                response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await response.prepare(request)
                chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture',
                         'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'pending'}, 'finish_reason': None}]}
                try:
                    await response.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                    await release_provider.wait()
                    chunk['choices'][0].update(delta={}, finish_reason='stop')
                    await response.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
                    await response.write_eof()
                except (ConnectionResetError, RuntimeError):
                    pass
                return response

            app = web.Application(); app.router.add_post('/v1/chat/completions', provider)
            runner = web.AppRunner(app); await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
            port = site._server.sockets[0].getsockname()[1]
            Path(directory, 'opencode.json').write_text(json.dumps({'provider': {'flowcanvas-fixture': {
                'npm': '@ai-sdk/openai-compatible', 'name': 'FlowCanvas fixture',
                'options': {'baseURL': f'http://127.0.0.1:{port}/v1', 'apiKey': 'nonsecret-test-fixture'},
                'models': {'fixture': {'name': 'Fixture', 'limit': {'context': 16000, 'output': 200}}}}}}))
            events = []

            def emit(event):
                """Retain the original native failure and observe a single projected terminal state."""
                events.append(event)
                if event['kind'] == 'turn' and event['status'] != 'inProgress': terminal.set()

            backend = OpenCodeBackend(directory, emit)
            original_http, original_event = backend.http, backend.event
            thread = steering = None
            abort_failed = False

            async def defer_failure(event):
                """Hold the actual native error until a guidance request is in transit."""
                props = event.get('properties', {})
                error = props.get('error') or props.get('info', {}).get('error')
                if error and error.get('name') != 'MessageAbortedError':
                    failed.set(); await release_failure.wait()
                await original_event(event)

            async def delay_guidance(method, path, body=None, **kwargs):
                """Pause the guidance HTTP dispatch without replacing native session behavior."""
                nonlocal abort_failed
                if method == 'POST' and path.endswith('/prompt_async'):
                    entered.set(); await release_guidance.wait()
                if abort_fault and path.endswith('/abort') and not abort_failed:
                    abort_failed = True
                    raise RuntimeError('Injected abort transport failure')
                return await original_http(method, path, body, **kwargs)

            try:
                backend.event = defer_failure
                model = 'flowcanvas-fixture/fixture'
                thread = await backend.open(None, model, 'full')
                native_process, native_readers = backend.process, set(backend.tasks)
                turn = await backend.send(thread, 'Reply with one word. Do not use tools.', model, 'default', mode='full')
                await asyncio.wait_for(failed.wait(), 30)
                backend.http = delay_guidance
                steering = asyncio.create_task(backend.steer(thread, turn, 'Use another word instead.'))
                await asyncio.wait_for(entered.wait(), 5)
                release_failure.set()
                await asyncio.sleep(.02)
                release_guidance.set()
                await asyncio.wait_for(steering, 20)
                await asyncio.wait_for(terminal.wait(), 20)
                if abort_fault:
                    self.assertTrue(abort_failed)
                self.assertIsNotNone(native_process.returncode)
                await backend.connect()
                self.assertNotEqual(backend.process.pid, native_process.pid)
                self.assertTrue(all(task.done() for task in native_readers))
                for _ in range(10):
                    await asyncio.sleep(.1)
                    states = await original_http('GET', '/session/status')
                    self.assertEqual(states.get(thread, {}).get('type', 'idle'), 'idle')
                states = [e for e in events if e['kind'] == 'turn']
                self.assertEqual([e['status'] for e in states], ['inProgress', 'failed'])
                self.assertIn('FC_LOCAL_REJECT_621', states[-1]['error'])
                self.assertNotIn(thread, backend.active)
                self.assertTrue(calls)
                terminal.clear(); release_provider.set()
                recovered = await backend.send(thread, 'Now reply successfully without tools.',
                                               model, 'default', mode='full')
                await asyncio.wait_for(terminal.wait(), 20)
                states = [e for e in events if e['kind'] == 'turn']
                self.assertEqual([e['status'] for e in states], ['inProgress', 'failed', 'inProgress', 'completed'])
                self.assertEqual(states[-1]['turn'], recovered)
                records = await original_http('GET', '/session/' + thread + '/message')
                self.assertTrue(any(row['info']['id'] == recovered and row['info']['role'] == 'user' for row in records))
                self.assertTrue(any(row['info'].get('parentID') == recovered and
                                    row['info'].get('time', {}).get('completed') for row in records))
                self.assertGreaterEqual(len(calls), 2)
                print('real native failure retained; late guidance canceled; same-session recovery; abort_fault=' + str(abort_fault), flush=True)
            finally:
                release_failure.set(); release_guidance.set(); release_provider.set()
                if steering and not steering.done(): steering.cancel()
                if steering: await asyncio.gather(steering, return_exceptions=True)
                backend.http, backend.event = original_http, original_event
                try:
                    if thread:
                        await backend.connect()
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                finally:
                    await backend.close(); await runner.cleanup()

    async def test_automatic_compaction(self):
        """Trigger native automatic compaction with a temporary input budget, then resume."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-compaction-') as directory:
            root = Path(directory)
            events, visible = [], {}
            terminal = asyncio.Event()

            def emit(event):
                """Retain turn boundaries and apply normal chat text projection semantics."""
                events.append(event)
                if event['kind'] == 'item' and event['item']['kind'] == 'text':
                    visible[event['item']['id']] = event['item']['text']
                elif event['kind'] == 'delta' and event['type'] == 'text':
                    visible[event['id']] = visible.get(event['id'], '') + event['text']
                elif event['kind'] == 'turn' and event['status'] != 'inProgress':
                    terminal.set()

            backend = OpenCodeBackend(root, emit)
            thread = None
            try:
                catalog = await backend.models()
                model = next((row for row in catalog if row['default']), catalog[0])
                effort = 'low' if 'low' in model['efforts'] else 'default'
                provider_id, model_id = model['id'].split('/', 1)
                providers = await backend.http('GET', '/provider')
                native_model = next(p for p in providers['all'] if p['id'] == provider_id)['models'][model_id]
                thread = await backend.open(None, model['id'], 'full')
                filler = '\n'.join(hashlib.sha256(str(index).encode()).hexdigest() for index in range(512))
                await backend.send(thread, 'Remember marker FC_COMPACT_471 and the constraint never modify files. '
                                   'The following fixture data is irrelevant and must not be preserved in summaries. '
                                   'Reply exactly READY_471. Do not use tools.\n<fixture>\n' + filler + '\n</fixture>',
                                   model['id'], effort, mode='full')
                await asyncio.wait_for(terminal.wait(), 120)
                self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'], ['inProgress', 'completed'])
                self.assertIn('READY_471', '\n'.join(visible.values()))
                history = await backend.http('GET', '/session/' + thread + '/message')
                first = next(row for row in reversed(history) if row['info']['role'] == 'assistant')
                tokens = first['info']['tokens']
                used = tokens.get('total') or sum(tokens.get(key, 0) for key in ('input', 'output')) + sum(tokens.get('cache', {}).values())
                self.assertGreater(used, 5000, 'Need real input usage above the compact summary size')
                print('compaction fixture input usage:', used, flush=True)

                # Only this temporary project's limit changes. Neither provider
                # credentials nor the normal model context are copied or edited.
                limits = dict(native_model['limit'], input=used - 1)
                # Custom providers may omit context size (zero disables native
                # compaction altogether). Supply one only for this test project.
                limits['context'] = limits.get('context') or used * 2 + limits.get('output', 0)
                (root / 'opencode.json').write_text(json.dumps({
                    'provider': {provider_id: {'models': {model_id: {'limit': limits}}}},
                    'compaction': {'auto': True, 'prune': False, 'tail_turns': 0, 'reserved': 0},
                }))
                await backend.close()
                await backend.open(thread, model['id'], 'full')
                refreshed = await backend.http('GET', '/provider')
                actual = next(p for p in refreshed['all'] if p['id'] == provider_id)['models'][model_id]['limit']
                self.assertEqual(actual['input'], used - 1)
                self.assertGreater(actual['context'], 0)
                config = await backend.http('GET', '/config')
                self.assertTrue(config['compaction']['auto'])
                events.clear(); visible.clear(); terminal.clear()
                turn = await backend.send(thread, 'Return the marker I asked you to remember, followed by the file modification constraint. '
                                          'No tools or other explanation.', model['id'], effort, mode='full')
                await asyncio.wait_for(terminal.wait(), 180)
                self.assertEqual([e['status'] for e in events if e['kind'] == 'turn'], ['inProgress', 'completed'])
                self.assertTrue(all(e.get('turn', turn) == turn for e in events))
                records = await backend.http('GET', '/session/' + thread + '/message')
                compactions = [part for row in records for part in row['parts'] if part['type'] == 'compaction']
                self.assertTrue(compactions, 'Native automatic compaction must actually run')
                self.assertTrue(all(part['auto'] for part in compactions))
                summaries = [row for row in records if row['info']['role'] == 'assistant' and row['info'].get('summary')]
                self.assertTrue(summaries)
                self.assertTrue(all(row['info'].get('time', {}).get('completed') and not row['info'].get('error') for row in summaries))
                continuations = [row for row in records if row['info']['role'] == 'user' and
                                 any(part['type'] == 'text' and part.get('synthetic') for part in row['parts'])]
                self.assertTrue(continuations, 'Native automatic continuation must remain in the same task')
                final = next(row for row in reversed(records) if row['info']['role'] == 'assistant' and not row['info'].get('summary'))
                final_text = {p['id']: p['text'] for p in final['parts'] if p['type'] == 'text'}
                self.assertIn('FC_COMPACT_471', '\n'.join(final_text.values()))
                self.assertIn('never modify files', '\n'.join(final_text.values()).lower())
                self.assertFalse(any(part['type'] == 'tool' for row in records for part in row['parts']))
                self.assertEqual(visible, final_text, 'Internal compaction summaries must not become ordinary chat replies')
                self.assertNotIn(thread, backend.active)
                print('automatic compaction, preserved requirements, single continuation and clean projection: passed', flush=True)
            finally:
                if thread:
                    try:
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                    except RuntimeError:
                        pass
                await backend.close()

    async def test_public_permission_change_queues_under_the_new_policy(self):
        """Change full/read-only/full through HTTP while a message arrives during reconnect."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-policy-') as directory:
            service = ChatService(directory)
            service.flusher = asyncio.create_task(service.flush())
            app = FastAPI(); app.include_router(routes(service))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            owner = None
            try:
                catalog = await service.backends['opencode'].models()
                service.catalogs[('opencode', str(service.workspace))] = catalog
                model = next((row for row in catalog if row['default']), catalog[0])
                effort = 'low' if 'low' in model['efforts'] else 'default'
                created = await client.post('/api/chat/sessions', json={'backend': 'opencode'})
                owner = service.get(created.json()['id'])
                endpoint = '/api/chat/sessions/' + owner['id']

                async def completed():
                    """Wait for native completion and child reconciliation before the next policy change."""
                    async with asyncio.timeout(150):
                        while owner['status'] != 'completed' or owner.get('queue') or owner.get('children_pending'):
                            self.assertNotIn(owner['status'], {'error', 'interrupted'}, owner.get('error'))
                            await asyncio.sleep(.1)

                self.assertEqual((await client.post(endpoint + '/permissions', json={'mode': 'full'})).status_code, 200)
                response = await client.post(endpoint + '/messages', json={
                    'text': 'Remember FC_POLICY_923. Use a file-writing tool to create allowed.txt containing FC_ALLOWED_923. '
                            'Then reply with the remembered marker. Do not invoke workflows.',
                    'model': model['id'], 'effort': effort})
                self.assertEqual(response.status_code, 202)
                self.assertEqual((await client.post(endpoint + '/permissions', json={'mode': 'full'})).status_code, 409)
                self.assertEqual((await client.post(endpoint + '/reconnect')).status_code, 409)
                await completed()
                self.assertEqual((service.workspace / 'allowed.txt').read_text().strip(), 'FC_ALLOWED_923')
                thread = owner['thread']
                old = service.session_backends[owner['id']]
                process = old.process
                closing, release = asyncio.Event(), asyncio.Event()
                native_close = old.close

                async def close():
                    """Expose the real reconnect boundary before terminating the old idle server."""
                    closing.set(); await release.wait(); await native_close()

                with patch.object(old, 'close', side_effect=close):
                    changing = asyncio.create_task(client.post(endpoint + '/permissions', json={'mode': 'full'}))
                    try:
                        await asyncio.wait_for(closing.wait(), 5)
                        response = await client.post(endpoint + '/messages', json={
                            'text': 'Attempt to create forbidden.txt containing SHOULD_NOT_EXIST using a writing tool. '
                                    'If permission is denied, stop trying. In your final reply include the marker I asked you to remember.',
                            'model': model['id'], 'effort': effort})
                        self.assertEqual(response.status_code, 202)
                        self.assertEqual(len(owner['queue']), 1)
                        self.assertIs(service.session_backends[owner['id']], old)
                        release.set()
                        self.assertEqual((await changing).status_code, 200)
                    finally:
                        release.set(); await asyncio.gather(changing, return_exceptions=True)
                await completed()
                current = service.session_backends[owner['id']]
                self.assertIsNot(current, old)
                self.assertIsNotNone(process.returncode)
                self.assertEqual(owner['thread'], thread)
                self.assertFalse((service.workspace / 'forbidden.txt').exists())
                self.assertEqual((service.workspace / 'allowed.txt').read_text().strip(), 'FC_ALLOWED_923')
                policy = (await current.http('GET', '/session/' + thread))['permission']
                self.assertIn({'permission': '*', 'pattern': '*', 'action': 'deny'}, policy)
                self.assertFalse(any(rule['permission'] in {'write', 'edit', 'bash', 'task'} and rule['action'] == 'allow' for rule in policy))
                replies = [row['text'] for row in owner['items'] if row['kind'] == 'text']
                self.assertIn('FC_POLICY_923', replies[-1])
                self.assertEqual(owner['permission_mode'], 'full')

                self.assertEqual((await client.post(endpoint + '/permissions', json={'mode': 'full'})).status_code, 200)
                response = await client.post(endpoint + '/messages', json={
                    'text': 'Use a file-writing tool to create restored.txt containing FC_RESTORED_923. Then reply DONE.',
                    'model': model['id'], 'effort': effort})
                self.assertEqual(response.status_code, 202)
                await completed()
                self.assertEqual(owner['thread'], thread)
                self.assertEqual((service.workspace / 'restored.txt').read_text().strip(), 'FC_RESTORED_923')
                self.assertFalse((service.workspace / 'forbidden.txt').exists())
                saved = json.loads((service.root / (owner['id'] + '.json')).read_text())
                self.assertEqual(saved['permission_mode'], 'full')
                self.assertEqual(sum(row['kind'] == 'user' for row in saved['items']), 3)
                print('public full/read-only/full transition, concurrent queued message and native history: passed', flush=True)
            finally:
                if owner and owner.get('thread'):
                    backend = service.session_backends[owner['id']]
                    backend.mcp_url = None
                    try:
                        await backend.connect()
                        await backend.http('POST', '/session/' + owner['thread'] + '/abort', {})
                        await backend.http('DELETE', '/session/' + owner['thread'])
                    except RuntimeError:
                        pass
                await client.aclose(); await service.close()

    async def test_native_model_selection(self):
        """Verify accepted native model selection, then cancel this configuration probe."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-selection-') as directory:
            backend = OpenCodeBackend(Path(directory), lambda event: None)
            thread = None
            try:
                models = await backend.models()
                model = next((m for m in models if m['default']), models[0])
                if 'low' not in model['efforts']:
                    self.skipTest('Configured model does not expose a low reasoning variant')
                thread = await backend.open(None, model['id'], 'full')
                turn = await backend.send(thread, 'Reply with exactly FC_VARIANT_OK. Do not use tools.',
                                          model['id'], 'low', mode='full')
                async with asyncio.timeout(10):
                    while True:
                        records = await backend.http('GET', '/session/' + thread + '/message')
                        user = next((row['info'] for row in records if row['info']['id'] == turn), None)
                        if user: break
                        await asyncio.sleep(.1)
                self.assertEqual(user['model']['variant'], 'low')
                self.assertEqual(user['model']['providerID'] + '/' + user['model']['modelID'], model['id'])
                print('native model selection and low reasoning variant: passed', flush=True)
            finally:
                if thread:
                    await backend.http('POST', '/session/' + thread + '/abort', {})
                    await backend.http('DELETE', '/session/' + thread)
                await backend.close()


    async def test_stream_observer_reconnect(self):
        """Drop the actual SSE response and verify automatic recovery preserves native output."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-stream-') as directory:
            terminal, streaming = asyncio.Event(), asyncio.Event()
            visible, current = [], {}
            states = []

            def emit(event):
                """Apply the same replace/append semantics as the chat projection."""
                if event['kind'] == 'item' and event['item']['kind'] == 'text':
                    current[event['item']['id']] = event['item']['text']
                    visible.append(dict(current))
                elif event['kind'] == 'delta' and event['type'] == 'text':
                    current[event['id']] = current.get(event['id'], '') + event['text']
                    visible.append(dict(current)); streaming.set()
                elif event['kind'] == 'turn':
                    states.append(event['status'])
                    if event['status'] != 'inProgress': terminal.set()

            backend = OpenCodeBackend(Path(directory), emit)
            responses = []
            native_get = aiohttp.ClientSession.get

            @asynccontextmanager
            async def observe_response(client, url, **kwargs):
                """Retain real event-stream responses so the test can close their connection."""
                async with native_get(client, url, **kwargs) as response:
                    responses.append(response)
                    yield response

            self.enterContext(patch.object(aiohttp.ClientSession, 'get', observe_response))
            thread = None
            try:
                models = await backend.models()
                model = next((row for row in models if row['default']), models[0])
                effort = 'low' if 'low' in model['efforts'] else 'default'
                thread = await backend.open(None, model['id'], 'full')
                await backend.send(thread, 'Write the integers from 1 to 250 in ascending order, each on a separate line. No tools, no explanation, no omissions.', model['id'], effort, mode='full')
                await asyncio.wait_for(streaming.wait(), 90)
                self.assertFalse(terminal.is_set(), 'Need an active stream to inject a transport gap')
                native_pid = backend.process.pid
                before = len(visible)
                self.assertEqual(len(responses), 1)
                responses[0].close()
                await asyncio.wait_for(terminal.wait(), 120)
                self.assertEqual(states[-1], 'completed')
                self.assertEqual(backend.process.pid, native_pid)
                self.assertGreaterEqual(len(responses), 2, 'The adapter must reconnect without a replacement reader')
                self.assertGreater(len(visible), before + 1, 'Output must keep updating after reconnect')
                records = await backend.http('GET', '/session/' + thread + '/message')
                self.assertEqual(sum(row['info']['role'] == 'user' for row in records), 1,
                                 'Reconnecting the observer must not resubmit the prompt')
                native_text = {part['id']: part['text'] for row in records if row['info']['role'] == 'assistant'
                               for part in row['parts'] if part['type'] == 'text' and not part.get('ignored')}
                self.assertEqual(current, native_text, 'Visible text must match authoritative native history')
                for snapshot in visible:
                    for identity, text in snapshot.items():
                        self.assertTrue(current[identity].startswith(text), 'Recovered text duplicated or rewound')
                self.assertTrue(any(text.strip().endswith('250') for text in current.values()))
                print('native HTTP stream loss and automatic snapshot recovery: passed', flush=True)
            finally:
                if thread:
                    try:
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                    except RuntimeError:
                        pass
                await backend.close()

    async def test_native_process_exit_and_resume(self):
        """Fail an owned process mid-reply, preserve partial output, and resume its history."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-process-') as directory:
            service = ChatService(directory)
            owner = service.create('opencode')
            terminal, streaming = asyncio.Event(), asyncio.Event()

            def emit(event):
                """Apply real native events to the durable conversation projection."""
                service.event({**event, 'session_id': owner['id']})
                if event['kind'] == 'delta' and event['type'] == 'text':
                    streaming.set()
                if event['kind'] == 'disconnected' or (event['kind'] == 'turn' and event['status'] != 'inProgress'):
                    terminal.set()

            backend = OpenCodeBackend(service.workspace, emit)
            service.session_backends[owner['id']] = backend
            thread = None
            try:
                models = await backend.models()
                model = next((row for row in models if row['default']), models[0])
                effort = 'low' if 'low' in model['efforts'] else 'default'
                thread = await backend.open(None, model['id'], 'full')
                owner.update(thread=thread, status='starting', permission_mode='full',
                             queue=[{'message': {'text': 'queued fixture'}, 'tier': None}])
                await backend.send(thread,
                    'Remember marker FC_PROCESS_RESUME_683. Write it first, then write the integers from 1 to 500, '
                    'one per line. Do not use any tools. No explanation, no omissions.',
                    model['id'], effort, mode='full')
                await asyncio.wait_for(streaming.wait(), 90)
                self.assertFalse(terminal.is_set(), 'Need a live reply to exercise process failure')
                self.assertTrue(any(row['status'] == 'running' for row in owner['items']))
                failed_pid = backend.process.pid
                backend.process.kill()
                await backend.process.wait()
                await asyncio.wait_for(terminal.wait(), 30)
                saved = json.loads((service.root / (owner['id'] + '.json')).read_text())
                self.assertEqual(saved['status'], 'error')
                self.assertTrue(saved['queue_paused'])
                self.assertEqual(len(saved['queue']), 1)
                self.assertTrue(saved['items'])
                self.assertTrue(all(row['status'] not in {'running', 'inProgress'} for row in saved['items']))
                partial = {row['id']: row['text'] for row in saved['items']}
                print('native process exit checkpoints partial output and pauses queue: passed', flush=True)

                terminal.clear()
                owner['status'] = 'starting'
                self.assertEqual(await backend.open(thread, model['id'], 'full'), thread)
                self.assertNotEqual(backend.process.pid, failed_pid)
                await backend.send(thread, 'Do not continue counting. What marker did I ask you to remember? '
                                   'Reply with that marker only, and do not use tools.', model['id'], effort, mode='full')
                await asyncio.wait_for(terminal.wait(), 120)
                self.assertEqual(owner['status'], 'completed', owner.get('error'))
                replies = [row for row in owner['items'] if row['kind'] == 'text' and row['id'] not in partial]
                self.assertTrue(any('FC_PROCESS_RESUME_683' in row['text'] for row in replies))
                self.assertTrue(all(row['text'] == partial[row['id']] for row in owner['items'] if row['id'] in partial))
                self.assertEqual(owner['thread'], thread)
                self.assertTrue(owner['queue_paused'])
                print('same native history resumes after process loss without replaying queued work: passed', flush=True)
            finally:
                if thread:
                    try:
                        await backend.connect()
                        await backend.http('POST', '/session/' + thread + '/abort', {})
                        await backend.http('DELETE', '/session/' + thread)
                    except RuntimeError:
                        pass
                await service.close()

    async def test_stop_terminates_native_command_and_preserves_queue(self):
        """Stop a real native shell tree through HTTP and verify durable task/queue state."""
        await self.check_native_command_shutdown(explicit_stop=True)

    async def test_close_terminates_native_command_and_preserves_queue(self):
        """Graceful service shutdown must stop owned commands and checkpoint interrupted work."""
        await self.check_native_command_shutdown(explicit_stop=False)

    async def test_shutdown_during_prompt_ack_cancels_native_work(self):
        """Close after native acceptance but before send returns, without orphaning its command."""
        await self.check_native_command_shutdown(explicit_stop=False, pending_ack=True)

    async def test_unsupported_event_terminates_native_command(self):
        """Inject a missing event field during a real command and require fatal cleanup."""
        await self.check_native_command_shutdown(explicit_stop=False, malformed_event=True)

    async def test_descendant_lookup_failure_still_stops_native_command(self):
        """A failed child lookup must not prevent cancellation of the live parent command."""
        await self.check_native_command_shutdown(explicit_stop=True, descendant_fault=True)

    async def check_native_command_shutdown(self, *, explicit_stop, pending_ack=False, malformed_event=False,
                                            descendant_fault=False):
        """Run an owned command tree and inspect real process exit plus saved chat state."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-stop-') as directory:
            service = ChatService(directory)
            service.flusher = asyncio.create_task(service.flush())
            script = service.workspace / 'owned_process_probe.py'
            script.write_text('''"""Disposable process tree for cancellation verification."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).parent
if '--leaf' in sys.argv:
    time.sleep(90)
else:
    child = subprocess.Popen([sys.executable, __file__, '--leaf'])
    (root / 'processes.json').write_text(json.dumps([os.getpid(), child.pid]))
    for tick in range(900):
        (root / 'heartbeat.txt').write_text(str(tick))
        print('FC_CANCEL_TICK', tick, flush=True)
        time.sleep(.1)
    child.wait()
    (root / 'unexpected-completion.txt').write_text('finished')
''')
            pids = []

            async def remaining_processes():
                """Identify still-running fixture processes without touching reused PIDs."""
                remaining = []
                for pid in pids:
                    probe = await asyncio.create_subprocess_exec('ps', '-p', str(pid), '-o', 'stat=', '-o', 'command=',
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                    stdout, _ = await probe.communicate()
                    status = stdout.decode().strip()
                    if status and not status.startswith('Z') and str(script) in status:
                        remaining.append(pid)
                return remaining

            app = FastAPI(); app.include_router(routes(service))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            owner = None
            accepted = asyncio.Event()
            original_http = OpenCodeBackend.http

            async def delayed_ack(backend, method, path, body=None, **kwargs):
                """Retain the real native request, delaying only return to its adapter caller."""
                result = await original_http(backend, method, path, body, **kwargs)
                if method == 'POST' and path.endswith('/prompt_async'):
                    accepted.set()
                    await asyncio.Future()
                return result

            delay = patch.object(OpenCodeBackend, 'http', new=delayed_ack)
            if pending_ack:
                delay.start()
            try:
                models = await service.backends['opencode'].models()
                service.catalogs[('opencode', str(service.workspace))] = models
                model = next((row for row in models if row['default']), models[0])
                effort = 'low' if 'low' in model['efforts'] else 'default'
                created = await client.post('/api/chat/sessions', json={'backend': 'opencode'})
                owner = service.get(created.json()['id'])
                owner['permission_mode'] = 'full'
                endpoint = '/api/chat/sessions/' + owner['id']
                command = shlex.quote(sys.executable) + ' ' + shlex.quote(str(script))
                response = await client.post(endpoint + '/messages', json={
                    'text': 'Use the shell tool to run exactly the following command and wait for it. '
                            'It is an owned temporary process fixture. Do not modify the script, run other commands, '
                            'or launch it in the background. After it finishes reply DONE.\n' + command,
                    'model': model['id'], 'effort': effort,
                    'task_card': {'title': 'Cancellation check', 'workflow': '', 'project': 'temporary test'}})
                self.assertEqual(response.status_code, 202)
                async with asyncio.timeout(120):
                    while not (service.workspace / 'processes.json').exists():
                        self.assertNotIn(owner['status'], {'error', 'interrupted', 'completed'}, owner.get('error'))
                        await asyncio.sleep(.1)
                pids.extend(json.loads((service.workspace / 'processes.json').read_text()))
                self.assertEqual(len(await remaining_processes()), 2)
                if pending_ack:
                    self.assertTrue(accepted.is_set())
                    self.assertIn(owner['id'], service.launching)
                async with asyncio.timeout(10):
                    while not any(row['kind'] == 'tool' and 'FC_CANCEL_TICK' in row['text'] for row in owner['items']):
                        await asyncio.sleep(.1)
                queued = await client.post(endpoint + '/messages', json={
                    'text': 'Reply only QUEUED_MUST_NOT_RUN.', 'model': model['id'], 'effort': effort})
                self.assertEqual(queued.status_code, 202)
                self.assertEqual(len(owner['queue']), 1)
                backend = service.session_backends[owner['id']]
                old_process = backend.process
                expected_status = 'error' if malformed_event or descendant_fault else 'interrupted'
                if malformed_event:
                    original_event = backend.event

                    async def malformed_next_event(event):
                        """Replace one real stream event with a missing-field fault in its reader."""
                        backend.event = original_event
                        await original_event({'type': 'message.updated', 'properties': {
                            'sessionID': owner['thread'], 'info': {}}})

                    backend.event = malformed_next_event
                elif explicit_stop:
                    original_backend_http = backend.http

                    async def unavailable_children(method, path, body=None, **kwargs):
                        """Fail only native child discovery, leaving actual cancellation available."""
                        if method == 'GET' and path.endswith('/children'):
                            raise RuntimeError('FC_CHILD_LOOKUP_451 unavailable')
                        return await original_backend_http(method, path, body, **kwargs)

                    if descendant_fault:
                        backend.http = unavailable_children
                    try:
                        stopped = await asyncio.wait_for(client.post(endpoint + '/stop'), 30)
                    finally:
                        backend.http = original_backend_http
                    self.assertEqual(stopped.status_code, 502 if descendant_fault else 200)
                    if descendant_fault:
                        print('child lookup failure stop result:', json.dumps({
                            'status': owner['status'], 'native_alive': old_process.returncode is None,
                            'owned_commands_alive': len(await remaining_processes())}), flush=True)
                        self.assertIsNotNone(old_process.returncode)
                        self.assertIn('FC_CHILD_LOOKUP_451', owner['error'])
                else:
                    await asyncio.wait_for(service.close(), 30)
                if malformed_event:
                    async with asyncio.timeout(20):
                        while owner['status'] in {'running', 'starting', 'stopping'}:
                            await asyncio.sleep(.1)
                    self.assertEqual(owner['status'], 'error')
                    self.assertIn('unsupported event', owner['error'])
                    print('fatal protocol result:', json.dumps({
                        'status': owner['status'], 'native_alive': old_process.returncode is None,
                        'owned_commands_alive': len(await remaining_processes())}), flush=True)
                async with asyncio.timeout(10):
                    while owner['status'] in {'running', 'starting', 'stopping'} or await remaining_processes():
                        await asyncio.sleep(.1)
                self.assertEqual(owner['status'], expected_status, owner.get('error'))
                if malformed_event:
                    self.assertIsNotNone(old_process.returncode)
                    self.assertIn('unsupported event', owner['error'])
                self.assertEqual(await remaining_processes(), [])
                heartbeat = (service.workspace / 'heartbeat.txt').read_text()
                await asyncio.sleep(1.1)
                self.assertEqual((service.workspace / 'heartbeat.txt').read_text(), heartbeat)
                self.assertFalse((service.workspace / 'unexpected-completion.txt').exists())
                saved = json.loads((service.root / (owner['id'] + '.json')).read_text())
                self.assertEqual(saved['status'], expected_status)
                self.assertTrue(saved['queue_paused'])
                self.assertEqual(len(saved['queue']), 1)
                self.assertTrue(saved['ended_at'])
                self.assertEqual(saved['items'][0]['task_run']['status'], expected_status)
                self.assertTrue(all(row['status'] not in {'running', 'inProgress'} for row in saved['items']))
                self.assertTrue(any(row['kind'] == 'tool' and 'FC_CANCEL_TICK' in row['text'] for row in saved['items']))
                backend = service.session_backends[owner['id']]
                if not explicit_stop and not malformed_event:
                    backend.mcp_url = None  # The test service and its MCP have already closed.
                await backend.connect()
                records = await backend.http('GET', '/session/' + owner['thread'] + '/message')
                self.assertEqual(sum(row['info']['role'] == 'user' for row in records), 1)
                print('real native command tree stopped; partial output, task card and paused queue persisted: passed', flush=True)
            finally:
                if pending_ack:
                    delay.stop()
                if owner and owner.get('thread'):
                    backend = service.session_backends[owner['id']]
                    try:
                        backend.mcp_url = None
                        await backend.connect()
                        await backend.http('POST', '/session/' + owner['thread'] + '/abort', {})
                        await backend.http('DELETE', '/session/' + owner['thread'])
                    except RuntimeError:
                        pass
                # A failed cancellation assertion must not leave its own fixture alive.
                for pid in await remaining_processes():
                    try: os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                await client.aclose()
                await service.close()

    async def test_flowcanvas_queue_and_resume(self):
        """Exercise public HTTP contracts, queued task cards and persisted backend selection."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-api-') as directory:
            service = ChatService(directory)
            service.flusher = asyncio.create_task(service.flush())
            app = FastAPI(); app.include_router(routes(service))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            try:
                models = await service.backends['opencode'].models()
                service.catalogs[('opencode', str(service.workspace))] = models
                model = next((m for m in models if m['default']), models[0])
                effort = 'low' if 'low' in model['efforts'] else 'default'
                owner = (await client.post('/api/chat/sessions', json={'backend': 'opencode'})).json()
                identity = owner['id']; endpoint = '/api/chat/sessions/' + identity
                first = await client.post(endpoint + '/messages', json={
                    'text': 'Remember API_MARKER_682. Reply only FIRST_OK.', 'model': model['id'], 'effort': effort,
                    'task_card': {'title': 'Integration check', 'workflow': '', 'project': 'temporary test'}})
                self.assertEqual(first.status_code, 202)
                second = await client.post(endpoint + '/messages', json={
                    'text': 'What marker did I ask you to remember? Reply with only the marker.', 'model': model['id'], 'effort': effort})
                self.assertEqual(second.status_code, 202)
                self.assertEqual(len(second.json()['queue']), 1)
                async with asyncio.timeout(150):
                    while True:
                        current = (await client.get(endpoint)).json()
                        if current['status'] in {'error', 'interrupted'}: self.fail(current.get('error') or current['status'])
                        if current['status'] == 'completed' and not current.get('queue'):
                            break
                        await asyncio.sleep(.1)
                replies = [i['text'] for i in current['items'] if i['kind'] == 'text']
                self.assertEqual(len(replies), 2)
                self.assertIn('FIRST_OK', replies[0])
                self.assertIn('API_MARKER_682', replies[1])
                self.assertEqual(current['items'][0]['task_run']['status'], 'completed')
                self.assertEqual(current['backend'], 'opencode')
                native = current['thread']
                await service.close()
                service = ChatService(directory)
                self.assertEqual(service.get(identity)['thread'], native)
                self.assertEqual(service.get(identity)['backend'], 'opencode')
                self.assertTrue(service.get(identity)['queue_paused'])
                # Remove only this test's native history through a temporary adapter.
                backend = service.backends['opencode']; await backend.connect()
                await backend.http('DELETE', '/session/' + native)
                print('FlowCanvas API, queue, task card and persistence: passed', flush=True)
            finally:
                await client.aclose()
                await service.close()


    async def test_public_testcase_run_monitoring(self):
        """Run one real failing Python fixture through MCP and verify the final evidence report."""
        await self.check_public_testcase_run_monitoring('assertion')

    async def test_public_testcase_coverage_failure_monitoring(self):
        """Keep a passing test separate from failed coverage collection in the real model report."""
        await self.check_public_testcase_run_monitoring('coverage')

    async def check_public_testcase_run_monitoring(self, scenario):
        """Exercise public task-card execution with either an assertion or post-processing failure."""
        from flowcanvas.projects import routes as project_routes
        from flowcanvas.testcases import Cases, execute
        from flowcanvas.test_runner import TestRuns, routes as run_routes
        from flowcanvas.coverage_runtime import state

        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-run-monitor-') as directory:
            root = Path(directory)
            project = root / 'project'; project.mkdir()
            coverage_failure = scenario == 'coverage'
            source = project / ('test_observed_collection.py' if coverage_failure else 'test_observed_failure.py')
            if coverage_failure:
                source.write_text('"""Local passing fixture for post-processing reporting, not a DUT test."""\n'
                    'def test_observed_collection():\n'
                    '    """Complete an ordinary assertion before coverage collection starts."""\n'
                    '    print("FC_MONITOR_STARTED", flush=True)\n'
                    '    assert 2 + 3 == 5\n'
                    '    print("FC_MONITOR_FINAL", flush=True)\n')
                collector = project / 'collection_probe.py'
                collector.write_text('"""Controlled collection failure for the model reporting integration."""\n'
                    'from pathlib import Path\nimport sys\nimport time\n'
                    'root = Path(__file__).parent\n'
                    'if sys.argv[1] == "merge":\n'
                    '    (root / "unexpected-merge").write_text("called")\n'
                    '    raise SystemExit(5)\n'
                    'print("FC_COLLECTION_WAITING", file=sys.stderr, flush=True)\n'
                    '(root / "collection-entered").write_text("ready")\n'
                    'deadline = time.monotonic() + 60\n'
                    'while not (root / "collection-release").exists():\n'
                    '    if time.monotonic() > deadline: raise TimeoutError("collection observation gate expired")\n'
                    '    time.sleep(.05)\n'
                    'print("FC_COVERAGE_SOURCE_MISSING", file=sys.stderr, flush=True)\n'
                    'raise SystemExit(2)\n')
            else:
                source.write_text('"""Deliberately failing fixture for the run-monitor integration, not a DUT test."""\n'
                    'import time\n\n'
                    'def test_observed_failure():\n'
                    '    """Emit live progress and a known mismatch for reporting checks."""\n'
                    '    print("FC_MONITOR_STARTED", flush=True)\n'
                    '    for tick in range(3):\n'
                    '        print(f"FC_MONITOR_TICK_{tick}", flush=True)\n'
                    '        time.sleep(1)\n'
                    '    actual, expected = 9, 10\n'
                    '    print("FC_MONITOR_FINAL", flush=True)\n'
                    '    assert actual == expected, "FC_ASSERTION_PROBE"\n')
            service = ChatService(root / 'state')
            service.test_runs = TestRuns()
            service.flusher = asyncio.create_task(service.flush())
            app = FastAPI(); app.include_router(routes(service))
            app.include_router(project_routes(service.projects))
            app.include_router(run_routes(service.projects, service.test_runs))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            owner = None
            try:
                response = await client.post('/api/chat/projects/import', json={'path': str(project), 'name': 'Run monitor fixture'})
                self.assertEqual(response.status_code, 200, response.text)
                identity = response.json()['id']
                execute(project, {'operation': 'configure', 'expected_revision': 0, 'values': {
                    'command': shlex.quote(sys.executable) + ' -m pytest -q -s {case}',
                    'description': 'Local Python integration fixture; no simulation or remote execution.'}})
                store = Cases(project)
                candidate = store.discover([source.name])['cases'][0]
                store.import_files([source.name], store.read().revision, [{**candidate,
                    'reason': 'Standalone pytest function with an actual assertion and progress output.',
                    'title': 'Observed collection fixture' if coverage_failure else 'Observed mismatch fixture',
                    'description': 'Check arithmetic before collecting configured coverage.' if coverage_failure else
                        'Emit progress, then compare actual 9 with expected 10.'}])
                if coverage_failure:
                    python = shlex.quote(sys.executable)
                    execute(project, {'operation': 'configure_coverage', 'expected_revision': store.read().revision,
                        'values': {'coverage': {'tool': 'local-fixture',
                            'description': 'Local collection fixture; report its result independently of pytest.',
                            'artifact_path': '{run_dir}/coverage.json',
                            'extract_command': python + ' collection_probe.py extract {run_dir}',
                            'merge_command': python + ' collection_probe.py merge {inputs_manifest} {merge_dir}',
                            'report_path': '{merge_dir}/report.json',
                            'build_id_command': python + ' -c ' + shlex.quote('print("fixture-build")')}}})
                case = store.read().targets[0]
                before = source.read_bytes()
                collector_before = collector.read_bytes() if coverage_failure else None
                response = await client.post('/api/chat/sessions', json={'backend': 'opencode'})
                self.assertEqual(response.status_code, 200, response.text)
                owner = service.get(response.json()['id'])
                response = await client.post('/api/chat/sessions/' + owner['id'] + '/permissions', json={'mode': 'full'})
                self.assertEqual(response.status_code, 200, response.text)
                models = await service.models('opencode', project)
                model = next((m for m in models if m['default']), models[0])
                response = await client.post('/api/chat/sessions/' + owner['id'] + '/messages', json={
                    'model': model['id'], 'effort': 'low' if 'low' in model['efforts'] else 'default',
                    'text': f'Run the existing case {case.id} in the current project {project}. '
                        'Use test_cases(read) to verify the ID and shared runner. Call test_case_runs(start, case_id) '
                        'exactly once and retain run.id. Monitor with test_case_runs(read, run_id, offset, wait_seconds=10), '
                        'advancing the returned offset until the run is terminal and no unread log remains. '
                        'Report an observed exception or assertion promptly; do not confuse a quiet interval with deadlock. '
                        'After completion, report the exact run ID, exit code, assertion evidence, and coverage availability. '
                        'This is an integration fixture, not evidence of a DUT defect. Do not edit files, retry, delegate, '
                        'start a workflow, or run commands outside the shared runner. Report in English. ' +
                        ('Report the test outcome and coverage collection outcome separately. End with two lines: '
                         'TEST_RESULT: passed|failed|unknown and COVERAGE_RESULT: collected|failed|unavailable, '
                         'selecting the actual outcome in each line.' if coverage_failure else
                         'End with RESULT: passed, RESULT: failed, or RESULT: unknown according to the actual run result.'),
                    'task_card': {'title': 'Run test case', 'workflow': '', 'project': 'Run monitor fixture',
                                  'requirements': case.name}})
                self.assertEqual(response.status_code, 202, response.text)
                started = last_report = asyncio.get_running_loop().time()
                async with asyncio.timeout(240):
                    while owner['status'] not in {'completed', 'error', 'interrupted'}:
                        now = asyncio.get_running_loop().time()
                        if coverage_failure and not (project / 'collection-release').exists():
                            observed = [i for i in owner['items'] if i['kind'] == 'tool'
                                        and i['title'] == 'ucagent_test_case_runs' and i['status'] == 'completed'
                                        and 'FC_COLLECTION_WAITING' in i.get('text', '')]
                            if observed:
                                self.assertIn('running', observed[-1]['text'])
                                (project / 'collection-release').touch()
                                print('model received collection progress before failure was released', flush=True)
                        if now - last_report >= 30:
                            last_report = now
                            print('run monitoring progress:', json.dumps({'elapsed': round(now - started),
                                'conversation': owner['status'], 'runs': [row['status'] for row in service.test_runs.list(project)],
                                'tools': [{'name': i['title'], 'status': i['status']}
                                          for i in owner['items'] if i['kind'] == 'tool'][-3:]}), flush=True)
                        await asyncio.sleep(.1)
                self.assertEqual(owner['status'], 'completed', owner.get('error'))
                response = await client.get('/api/chat/projects/' + identity + '/test-runs')
                self.assertEqual(response.status_code, 200, response.text)
                runs = response.json(); self.assertEqual(len(runs), 1)
                run = runs[0]
                self.assertEqual((run['status'], run['exit_code'], run['case_id']),
                                 ('completed', 0, case.id) if coverage_failure else ('failed', 1, case.id))
                self.assertEqual(owner['ai_test_run']['run']['id'], run['id'])
                self.assertEqual(owner['ai_test_run']['project']['id'], identity)
                output = (await client.get('/api/chat/projects/' + identity + '/test-runs/' + run['id'] + '/output')).json()
                self.assertIn('FC_MONITOR_STARTED', output['text'])
                self.assertIn('FC_MONITOR_FINAL', output['text'])
                self.assertIn('FC_COVERAGE_SOURCE_MISSING' if coverage_failure else 'FC_ASSERTION_PROBE', output['text'])
                tools = [row for row in owner['items'] if row['kind'] == 'tool' and row['title'] == 'ucagent_test_case_runs']
                inputs = [json.loads(row['input']) for row in tools]
                self.assertEqual(sum(row.get('operation') == 'start' for row in inputs), 1)
                reads = [row for row in inputs if row.get('operation') == 'read']
                self.assertTrue(reads)
                self.assertTrue(all(row['run_id'] == run['id'] for row in reads))
                self.assertEqual(reads[0].get('offset', 0), 0)
                self.assertEqual(reads[-1]['offset'], output['offset'], 'The agent did not verify that the final log was drained')
                self.assertTrue(all(row['status'] == 'completed' for row in tools))
                reply = '\n'.join(row['text'] for row in owner['items'] if row['kind'] == 'text')
                self.assertIn(run['id'], reply)
                if coverage_failure:
                    self.assertTrue((project / 'collection-release').is_file(), 'The agent did not observe ongoing collection')
                    self.assertEqual(run['coverage_status'], 'failed')
                    self.assertIn('exit 2', run['coverage_error'])
                    self.assertEqual(state(project)['runs'], [])
                    self.assertFalse((project / 'unexpected-merge').exists())
                    self.assertEqual(collector.read_bytes(), collector_before)
                    self.assertIn('FC_COVERAGE_SOURCE_MISSING', reply)
                    self.assertTrue(reply.strip().lower().endswith('test_result: passed\ncoverage_result: failed'), reply[-1000:])
                else:
                    self.assertIn('FC_ASSERTION_PROBE', reply)
                    self.assertTrue(reply.strip().lower().endswith('result: failed'), reply[-1000:])
                self.assertEqual(source.read_bytes(), before)
                self.assertFalse(owner.get('workflow_runs'))
                saved = json.loads((service.root / (owner['id'] + '.json')).read_text())
                self.assertEqual(saved['items'][0]['task_run']['status'], 'completed')
                print('actual run monitoring result:', json.dumps({'scenario': scenario, 'model': model['id'],
                    'run_count': len(runs), 'run_status': run['status'], 'coverage_status': run.get('coverage_status'),
                    'exit_code': run['exit_code'], 'read_calls': len(reads), 'final_offset': output['offset'],
                    'task_status': saved['items'][0]['task_run']['status']}), flush=True)
                print('run report:', reply[-1800:], flush=True)
            finally:
                await client.aclose()
                await service.test_runs.close()
                await service.close()
                if owner and owner.get('thread'):
                    backend = service.backends['opencode']
                    try:
                        await backend.connect()
                        await backend.http('POST', '/session/' + owner['thread'] + '/abort', {})
                        await backend.http('DELETE', '/session/' + owner['thread'])
                    finally:
                        await backend.close()

    async def test_public_testcase_deletion_preserves_shared_code(self):
        """Delete one bound case with a real model and run its unchanged shared-code sibling."""
        from flowcanvas.projects import routes as project_routes
        from flowcanvas.testcases import Cases, execute
        from flowcanvas.test_runner import TestRuns

        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-delete-case-') as directory:
            root = Path(directory); project = root / 'project'; project.mkdir()
            driver = project / 'driver.py'
            driver.write_text('"""Shared byte-lane fixture for two local tests, not a hardware model."""\n'
                'class Word:\n'
                '    """Apply writes only to the selected byte lanes."""\n'
                '    def __init__(self, value=0):\n'
                '        """Initialize the fixture word."""\n'
                '        self.value = value\n'
                '    def write(self, value, mask):\n'
                '        """Replace enabled bytes and retain disabled bytes."""\n'
                '        for lane in range(4):\n'
                '            if mask & (1 << lane):\n'
                '                bits = 0xff << (8 * lane)\n'
                '                self.value = (self.value & ~bits) | (value & bits)\n')
            source = project / 'test_words.py'
            source.write_text('"""Two cases share the Word driver; keep unrelated cases intact."""\n'
                'from driver import Word\n\n'
                'def _full_word_pattern():\n'
                '    """Supply the pattern used only by the full-word case."""\n'
                '    return 0x12345678\n\n'
                'def test_full_word_write():\n'
                '    """Replace all four byte lanes."""\n'
                '    word = Word()\n'
                '    word.write(_full_word_pattern(), 15)\n'
                '    assert word.value == _full_word_pattern()\n\n'
                'def test_low_byte_write():\n'
                '    """Update the low byte while retaining the other three."""\n'
                '    word = Word(0x11223344)\n'
                '    word.write(0xffeedd55, 1)\n'
                '    assert word.value == 0x11223355\n')
            service = ChatService(root / 'state'); service.test_runs = TestRuns()
            service.flusher = asyncio.create_task(service.flush())
            app = FastAPI(); app.include_router(routes(service)); app.include_router(project_routes(service.projects))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            owner = None
            try:
                imported = await client.post('/api/chat/projects/import', json={'path': str(project), 'name': 'Deletion fixture'})
                self.assertEqual(imported.status_code, 200, imported.text)
                execute(project, {'operation': 'configure', 'expected_revision': 0, 'values': {
                    'command': shlex.quote(sys.executable) + ' -m pytest -q {case}',
                    'description': 'Run the selected local assertion fixture; no hardware simulation.'}})
                store = Cases(project)
                candidates = store.discover([source.name])['cases']
                self.assertEqual(len(candidates), 2)
                store.import_files([source.name], store.read().revision, [{**candidate,
                    'reason': 'Independent pytest function with stimulus and an actual value assertion.',
                    'title': 'Full word replacement' if candidate['name'] == 'test_full_word_write' else 'Low byte update',
                    'description': 'Exercise shared byte-lane writes and check the resulting word.'} for candidate in candidates])
                definition = store.read()
                removed = next(row for row in definition.targets if row.name == 'test_full_word_write')
                retained = next(row for row in definition.targets if row.name == 'test_low_byte_write')
                before_source = source.read_text(); before_driver = driver.read_bytes()
                retained_before = next(node for node in ast.parse(before_source).body
                                       if isinstance(node, ast.FunctionDef) and node.name == retained.name)
                # Establish real historical evidence before asking the model to delete the case.
                baseline = await service.test_runs.start(project, removed.id)
                await asyncio.wait_for(service.test_runs.tasks[baseline['id']], 30)
                old_output = service.test_runs.output(project, baseline['id'])
                self.assertEqual((old_output['run']['status'], old_output['run']['exit_code']), ('completed', 0))
                history = {path: path.read_bytes() for path in service.test_runs.folder(project).glob(baseline['id'] + '.*')}
                response = await client.post('/api/chat/sessions', json={'backend': 'opencode'})
                self.assertEqual(response.status_code, 200, response.text)
                owner = service.get(response.json()['id'])
                response = await client.post('/api/chat/sessions/' + owner['id'] + '/permissions', json={'mode': 'full'})
                self.assertEqual(response.status_code, 200, response.text)
                catalog = await service.models('opencode', project)
                model = next((row for row in catalog if row['default']), catalog[0])
                response = await client.post('/api/chat/sessions/' + owner['id'] + '/messages', json={
                    'model': model['id'], 'effort': 'low' if 'low' in model['efforts'] else 'default',
                    'text': f'Delete only test case {removed.id} ({removed.name}) from project {project}. '
                        'First call test_cases(read) for the current revision and immediately call '
                        'test_cases(begin_delete, target_id, expected_revision) before inspecting source or deletion scope. '
                        'If the runner rejects deletion because the case is active, stop and explain. '
                        'After registration, read guide and inspect bindings and references. Remove the selected test '
                        'implementation and helpers used only by it, preserving shared drivers, other tests, '
                        'their imports, and historical runs and logs. Do not delete a file containing retained definitions. '
                        'After source cleanup, call test_cases(delete, target_id, expected_revision) for only this ID. '
                        'Do not edit project JSON directly, run tests or simulation, delegate, or start a workflow. '
                        'Report actual changes and retained shared code in English. Do not claim the remaining test passed without running it.',
                    'task_card': {'title': 'Delete test case', 'workflow': '', 'project': 'Deletion fixture', 'requirements': removed.name}})
                self.assertEqual(response.status_code, 202, response.text)
                observed_deleting = False
                started = last_report = asyncio.get_running_loop().time()
                async with asyncio.timeout(240):
                    while owner['status'] not in {'completed', 'error', 'interrupted'}:
                        current = next((row for row in store.read().targets if row.id == removed.id), None)
                        if current and current.lifecycle == 'deleting':
                            observed_deleting = True
                            self.assertEqual((current.operation_status, current.operation_session), ('running', owner['id']))
                        now = asyncio.get_running_loop().time()
                        if now - last_report >= 30:
                            last_report = now
                            print('case deletion progress:', json.dumps({'elapsed': round(now - started),
                                'conversation': owner['status'], 'deleting_observed': observed_deleting,
                                'tools': [{'name': item['title'], 'status': item['status']}
                                          for item in owner['items'] if item['kind'] == 'tool'][-3:]}), flush=True)
                        await asyncio.sleep(.1)
                self.assertEqual(owner['status'], 'completed', owner.get('error'))
                self.assertTrue(observed_deleting, 'No in-progress deletion was observed')
                calls = [item for item in owner['items'] if item['kind'] == 'tool' and item['title'] == 'ucagent_test_cases']
                arguments = [json.loads(item['input']) for item in calls]
                self.assertEqual([arg['operation'] for arg in arguments[:2]], ['read', 'begin_delete'])
                for operation in ('begin_delete', 'delete'):
                    selected = [arg for arg in arguments if arg['operation'] == operation]
                    self.assertEqual(len(selected), 1)
                    self.assertEqual(selected[0]['target_id'], removed.id)
                self.assertTrue(all(item['status'] == 'completed' for item in calls))
                after = store.read()
                self.assertEqual(after.revision, definition.revision + 2)
                self.assertEqual([row.model_dump() for row in after.targets], [retained.model_dump()])
                self.assertEqual(store.status()['reports'][retained.id]['status'], 'bound')
                self.assertEqual(driver.read_bytes(), before_driver)
                after_source = source.read_text(); parsed = ast.parse(after_source)
                names = [node.name for node in parsed.body if isinstance(node, ast.FunctionDef)]
                self.assertNotIn(removed.name, names)
                self.assertNotIn('_full_word_pattern', names)
                self.assertNotIn(removed.bindings[0].id, after_source)
                retained_after = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == retained.name)
                self.assertEqual(ast.get_source_segment(after_source, retained_after), ast.get_source_segment(before_source, retained_before))
                self.assertEqual({path: path.read_bytes() for path in history}, history)
                self.assertEqual(len(service.test_runs.list(project)), 1, 'The model started another test run')
                self.assertFalse(owner.get('workflow_runs'))
                saved = json.loads((service.root / (owner['id'] + '.json')).read_text())
                self.assertEqual(saved['items'][0]['task_run']['status'], 'completed')
                # The harness, not the deletion task, verifies the surviving bound entry through the managed runner.
                check = await service.test_runs.start(project, retained.id)
                await asyncio.wait_for(service.test_runs.tasks[check['id']], 30)
                output = service.test_runs.output(project, check['id'])
                self.assertEqual((output['run']['status'], output['run']['exit_code']), ('completed', 0), output['text'])
                self.assertIn('1 passed', output['text'])
                reply = '\n'.join(item['text'] for item in owner['items'] if item['kind'] == 'text')
                self.assertIn(removed.name, reply)
                print('actual deletion result:', json.dumps({'model': model['id'], 'deleting_observed': observed_deleting,
                    'remaining_cases': len(after.targets), 'retained_binding': store.status()['reports'][retained.id]['status'],
                    'shared_driver_unchanged': True, 'retained_function_unchanged': True, 'history_unchanged': True,
                    'post_delete_run_status': output['run']['status'], 'post_delete_exit_code': output['run']['exit_code']}), flush=True)
                print('deletion report:', reply[-2000:], flush=True)
            finally:
                await client.aclose(); await service.test_runs.close(); await service.close()
                if owner and owner.get('thread'):
                    backend = service.backends['opencode']
                    try:
                        await backend.connect()
                        await backend.http('POST', '/session/' + owner['thread'] + '/abort', {})
                        await backend.http('DELETE', '/session/' + owner['thread'])
                    finally:
                        await backend.close()

    async def test_public_testcase_authoring(self):
        """Create a real bound Python case through the same APIs as an AI task card."""
        from flowcanvas.projects import routes as project_routes
        from flowcanvas.testcases import Cases, execute, routes as case_routes
        from flowcanvas.test_runner import TestRuns

        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-authoring-') as directory:
            root = Path(directory)
            project = root / 'project'; project.mkdir()
            rtl = project / 'Probe.v'
            rtl.write_text('module Probe(input wire a, output wire y); assign y = ~a; endmodule\n')
            driver = project / 'driver.py'
            driver.write_text('"""Tiny byte-mask device fixture for authoring integration, not a DUT simulation."""\n'
                'class MaskedWord:\n'
                '    """Store a 32-bit word and update selected byte lanes."""\n'
                '    def __init__(self, value):\n'
                '        """Initialize the fixture with a known word."""\n'
                '        self.value = value & 0xffffffff\n'
                '    def write(self, value, mask):\n'
                '        """Replace only the byte lanes enabled in the four-bit mask."""\n'
                '        for lane in range(4):\n'
                '            if mask & (1 << lane):\n'
                '                bits = 0xff << (8 * lane)\n'
                '                self.value = (self.value & ~bits) | (value & bits)\n')
            seed = project / 'test_existing.py'
            seed.write_text('"""Existing full-mask scenario to preserve during authoring."""\n'
                'from driver import MaskedWord\n\n'
                'def test_full_mask_replaces_word():\n'
                '    """A full mask replaces every byte."""\n'
                '    device = MaskedWord(0x12345678)\n'
                '    device.write(0x87654321, 0xf)\n'
                '    assert device.value == 0x87654321\n')
            service = ChatService(root / 'state')
            service.test_runs = TestRuns()
            service.flusher = asyncio.create_task(service.flush())
            app = FastAPI(); app.include_router(routes(service))
            app.include_router(project_routes(service.projects))
            app.include_router(case_routes(service.projects, service.sessions))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            owner = None
            try:
                response = await client.post('/api/chat/projects/import', json={
                    'path': str(project), 'name': 'Authoring fixture', 'top': 'Probe', 'top_file': 'Probe.v'})
                self.assertEqual(response.status_code, 200, response.text)
                identity = response.json()['id']
                execute(project, {'operation': 'configure', 'expected_revision': 0, 'values': {
                    'command': shlex.quote(sys.executable) + ' -m pytest -q {case}',
                    'description': 'Local Python fixture; run from the project root using the configured interpreter.'}})
                store = Cases(project)
                candidate = store.discover(['test_existing.py'])['cases'][0]
                store.import_files(['test_existing.py'], store.read().revision, [{**candidate,
                    'reason': 'Standalone pytest function with an explicit full-mask assertion.',
                    'title': 'Full-mask replacement', 'description': 'Write all four byte lanes and check the resulting word.'}])
                original = {path: path.read_bytes() for path in (driver, seed, rtl)}
                seed_id = store.read().targets[0].id
                response = await client.post('/api/chat/sessions', json={'backend': 'opencode'})
                self.assertEqual(response.status_code, 200, response.text)
                owner = service.get(response.json()['id'])
                response = await client.post('/api/chat/sessions/' + owner['id'] + '/permissions', json={'mode': 'full'})
                self.assertEqual(response.status_code, 200, response.text)
                response = await client.post('/api/chat/sessions/' + owner['id'] + '/attachment', json={
                    'workflow_id': 'generate-testcase', 'task_only': True, 'dut': 'Probe', 'rtl_file': str(rtl)})
                self.assertEqual(response.status_code, 200, response.text)
                models = await service.models('opencode', project)
                model = next((m for m in models if m['default']), models[0])
                response = await client.post('/api/chat/sessions/' + owner['id'] + '/messages', json={
                    'model': model['id'], 'effort': 'low' if 'low' in model['efforts'] else 'default',
                    'text': f'Create one test case in the current project {project}. First call run_workflow for the '
                        'attached generate-testcase workflow and complete its actual stages, journals, Complete and Exit. '
                        'Read test_cases(guide/read) and existing tests. Reuse driver.MaskedWord without modifying driver.py '
                        'or test_existing.py. Probe.v is only the workflow input fixture: do not modify it or claim a hardware simulation. '
                        'In new file test_zero_mask.py implement test_zero_mask_preserves_word: '
                        'initialize a nonzero word, write a different value with mask=0, and assert the original word remains. '
                        'Register test_cases(create) BEFORE writing code, retain that same ID for update/bind and finish_create. '
                        'Use a concise Chinese title describing the scenario and a concrete description. Do not use import '
                        'to create a duplicate. Preserve the existing runner and case. Do not run tests or simulations, '
                        'do not delegate, and do not edit other project files. Report the actual new case and unverified items.',
                    'task_card': {'title': 'Create test case', 'workflow': 'generate-testcase',
                                  'project': 'Authoring fixture', 'requirements': 'Zero mask must preserve the original word.'}})
                self.assertEqual(response.status_code, 202, response.text)
                started = last_report = asyncio.get_running_loop().time()
                placeholders = set()
                async with asyncio.timeout(600):
                    while True:
                        data = store.read()
                        placeholders.update(row.id for row in data.targets if row.lifecycle == 'creating')
                        if owner['status'] == 'error': self.fail(owner['error'])
                        cards = owner.get('workflow_runs', [])
                        if cards and all(c['status'] == 'completed' for c in cards) and owner['status'] == 'completed':
                            break
                        if owner['status'] == 'completed' and not owner.get('children_pending'):
                            self.fail('Authoring ended without workflow acceptance: ' + str([c['status'] for c in cards]))
                        now = asyncio.get_running_loop().time()
                        if now - last_report >= 30:
                            last_report = now
                            print('authoring progress:', json.dumps({'elapsed': round(now - started),
                                'status': owner['status'], 'cards': [c['status'] for c in cards],
                                'cases': [{'name': row.name, 'lifecycle': row.lifecycle} for row in data.targets],
                                'tools': [{'name': i['title'], 'status': i['status']}
                                          for i in owner['items'] if i['kind'] == 'tool'][-3:]}), flush=True)
                        await asyncio.sleep(.1)
                result = (await client.get('/api/chat/projects/' + identity + '/testcases')).json()
                self.assertEqual(len(result['targets']), 2)
                created = next(row for row in result['targets'] if row['id'] != seed_id)
                self.assertEqual(placeholders, {created['id']})
                self.assertEqual(created['lifecycle'], 'ready')
                self.assertEqual(created['name'], 'test_zero_mask_preserves_word')
                self.assertTrue(created['title'].strip())
                self.assertNotEqual(created['title'], created['name'])
                self.assertTrue(created['description'].strip())
                self.assertEqual(result['reports'][created['id']]['status'], 'bound')
                self.assertEqual([b['file'] for b in created['bindings']], ['test_zero_mask.py'])
                tree = ast.parse((project / 'test_zero_mask.py').read_text())
                function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == created['name'])
                self.assertTrue(any(isinstance(node, ast.Assert) for node in ast.walk(function)))
                for path, content in original.items(): self.assertEqual(path.read_bytes(), content)
                self.assertEqual(service.test_runs.list(str(project)), [])
                self.assertTrue((Path(cards[0]['root']) / 'complete.json').is_file())
                self.assertTrue((Path(cards[0]['workspace']) / 'summary/testcase_result.md').is_file())
                task = next(item['task_run'] for item in owner['items'] if item.get('task_card'))
                self.assertEqual(task['status'], 'completed')
                self.assertFalse(owner.get('attachment'), 'Task-only attachment must not remain on the composer')
                self.assertFalse([item for item in owner['items'] if item['kind'] == 'tool'
                                  and item['status'] == 'failed' and item['text'] == 'Not connected'])
                print('actual authoring result:', json.dumps({'placeholder_count': len(placeholders),
                    'case_count': len(result['targets']), 'binding': result['reports'][created['id']]['status'],
                    'lifecycle': created['lifecycle'], 'task_status': task['status'], 'runs': 0}), flush=True)
            finally:
                await client.aclose()
                await service.test_runs.close()
                await service.close()
                if owner and owner.get('thread'):
                    backend = service.backends['opencode']
                    try:
                        await backend.connect()
                        await backend.http('POST', '/session/' + owner['thread'] + '/abort', {})
                        await backend.http('DELETE', '/session/' + owner['thread'])
                    finally:
                        await backend.close()

    async def test_authorized_workflow_acceptance(self):
        """Run a disposable RTL-summary workflow through the real host acceptance gate."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-opencode-workflow-') as directory:
            root = Path(directory)
            rtl = root / 'Probe.v'
            rtl.write_text('module Probe(input wire a, output wire y); assign y = ~a; endmodule\n')
            service = ChatService(root / 'state')
            service.projects.state['current'] = {'path': str(root)}
            service.flusher = asyncio.create_task(service.flush())
            app = FastAPI(); app.include_router(routes(service))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            owner = None
            try:
                models = await service.backends['opencode'].models()
                service.catalogs[('opencode', str(service.workspace))] = models
                model = next((m for m in models if m['default']), models[0])
                response = await client.post('/api/chat/workflows/direct', json={
                    'backend': 'opencode', 'workflow_id': 'rtl-summary', 'rtl_file': str(rtl), 'dut': 'Probe',
                    'model': model['id'], 'effort': 'low' if 'low' in model['efforts'] else 'default'})
                self.assertEqual(response.status_code, 200, response.text)
                owner = service.get(response.json()['id'])
                started = last_report = asyncio.get_running_loop().time()
                # A real workflow spans multiple model/tool steps; provider latency
                # must not cut off an otherwise progressing acceptance sequence.
                async with asyncio.timeout(600):
                    while True:
                        if owner['status'] == 'error': self.fail(owner['error'])
                        cards = owner.get('workflow_runs', [])
                        if cards and all(c['status'] == 'completed' for c in cards) and owner['status'] == 'completed':
                            break
                        if owner['status'] == 'completed' and not owner.get('children_pending'):
                            self.fail('Workflow ended without host acceptance: ' + str([(c['status'], c.get('pause_reason')) for c in cards]) + '\n' + '\n'.join(i.get('text', '') for i in owner['items'] if i['kind'] == 'text'))
                        now = asyncio.get_running_loop().time()
                        if now - last_report >= 30 and owner.get('thread'):
                            last_report = now
                            backend = service.session_backends.get(owner['id'])
                            if backend:
                                states = await backend.http('GET', '/session/status')
                                print('workflow progress:', json.dumps({
                                    'elapsed': round(now - started),
                                    'native': states.get(owner['thread'], {}).get('type', 'idle'),
                                    'cards': [c['status'] for c in cards],
                                    'tools': [{'name': i['title'], 'status': i['status']}
                                              for i in owner['items'] if i['kind'] == 'tool'][-4:]
                                }), flush=True)
                        await asyncio.sleep(.25)
                self.assertEqual(len(cards), 1)
                card = cards[0]
                self.assertTrue((Path(card['root']) / 'complete.json').is_file())
                summary = Path(card['report_file'])
                self.assertTrue(summary.is_file())
                self.assertTrue(summary.read_text().strip())
                self.assertEqual(rtl.read_text(), 'module Probe(input wire a, output wire y); assign y = ~a; endmodule\n')
                exits = [item for item in owner['items'] if item['kind'] == 'tool' and item['title'] == 'ucagent_Exit']
                self.assertTrue(exits, 'The workflow must execute its final Exit tool')
                self.assertTrue(all(item['status'] == 'completed' for item in exits), 'Exit must return its result before shutdown')
                print('authorized workflow, stage tools and host acceptance: passed', flush=True)
            except TimeoutError:
                # Report bounded identities/states, never raw provider configuration.
                bridge = service.workflow_bridges.get(owner['id']) if owner else None
                print('workflow timeout:', json.dumps({
                    'session_status': owner.get('status') if owner else None,
                    'bridge_call_active': bool(bridge and bridge.call),
                    'cards': [c['status'] for c in owner.get('workflow_runs', [])] if owner else [],
                    'tasks': [{'name': task.get_coro().__qualname__,
                               'frames': [frame.f_code.co_name for frame in task.get_stack(limit=2)]}
                              for task in asyncio.all_tasks() if task is not asyncio.current_task()]
                }), flush=True)
                backend = service.session_backends.get(owner['id']) if owner else None
                if backend and owner.get('thread'):
                    records = await backend.http('GET', '/session/' + owner['thread'] + '/message')
                    print('native message progress:', json.dumps([
                        {'role': row['info']['role'], 'time': row['info'].get('time', {}),
                         'finish': row['info'].get('finish'),
                         'error_type': row['info'].get('error', {}).get('name'),
                         'parts': [{'type': part['type'], 'text_length': len(part.get('text', ''))}
                                   for part in row['parts'][-8:]]}
                        for row in records[-4:]
                    ]), flush=True)
                    print('native tool states:', json.dumps([
                        {'tool': p.get('tool'), 'status': p.get('state', {}).get('status'),
                         'input_fields': list(p.get('state', {}).get('input', {}))}
                        for row in records for p in row['parts'] if p['type'] == 'tool'
                    ][-8:]), flush=True)
                raise
            finally:
                await client.aclose()
                await service.close()
                if owner and owner.get('thread'):
                    # The catalog handoff may already have released the execution
                    # client. Delete owned test history after all handoffs stop.
                    backend = service.backends['opencode']
                    try:
                        await backend.connect()
                        await backend.http('POST', '/session/' + owner['thread'] + '/abort', {})
                        await backend.http('DELETE', '/session/' + owner['thread'])
                    finally:
                        await backend.close()


if __name__ == '__main__':
    unittest.main()
