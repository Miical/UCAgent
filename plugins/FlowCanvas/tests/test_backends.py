"""Backend-independent API regressions and OpenCode event edge cases, without model calls."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import aiohttp
from fastapi import FastAPI
import httpx

from flowcanvas.backends.opencode import OpenCodeBackend, item, permissions
from flowcanvas.chat import ChatService, routes
from flowcanvas.workflow_runtime import WorkflowRuntime


class BackendAPITests(unittest.IsolatedAsyncioTestCase):
    """Prevent backend selection from mixing native histories or model catalogs."""

    async def asyncSetUp(self):
        """Isolate persistence and use local ASGI transport with synthetic model catalogs."""
        self.temp = tempfile.TemporaryDirectory()
        self.service = ChatService(self.temp.name)
        self.app = FastAPI()
        self.app.include_router(routes(self.service))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        self.service.backends['codex'].models = AsyncMock(return_value=[{'id': 'codex-model', 'efforts': ['low']}])
        self.service.backends['opencode'].models = AsyncMock(return_value=[{'id': 'provider/model', 'efforts': ['default', 'low']}])

    async def asyncTearDown(self):
        """Reap service-owned jobs before removing temporary state."""
        await self.client.aclose()
        await self.service.close()
        self.temp.cleanup()

    async def test_catalog_failure_is_isolated(self):
        """An offline Codex installation must not hide a configured OpenCode backend."""
        self.service.backends['codex'].models.side_effect = RuntimeError('CLI unavailable')
        response = await self.client.get('/api/chat/backends')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data[0]['models'], [])
        self.assertEqual(data[1]['models'][0]['id'], 'provider/model')

    async def test_history_owner_and_catalog_enforced(self):
        """Backend switching is only permitted before native history exists."""
        await self.client.get('/api/chat/backends')
        created = (await self.client.post('/api/chat/sessions', json={'backend': 'opencode'})).json()
        identity = created['id']; endpoint = '/api/chat/sessions/' + identity
        self.assertEqual(created['backend'], 'opencode')
        invalid = await self.client.post(endpoint + '/messages', json={'text': 'hello', 'model': 'codex-model', 'effort': 'low'})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual((await self.client.post(endpoint + '/backend', json={'backend': 'codex'})).status_code, 200)
        self.service.sessions[identity]['thread'] = 'native-codex-thread'
        self.assertEqual((await self.client.post(endpoint + '/backend', json={'backend': 'opencode'})).status_code, 409)
        self.assertEqual(self.service.sessions[identity]['backend'], 'codex')
        self.assertEqual((await self.client.post('/api/chat/sessions', json={'backend': 'unknown'})).status_code, 422)

    async def test_events_never_cross_session_boundaries(self):
        """Even colliding native IDs cannot attach one backend's events to another session."""
        first = self.service.create('codex'); second = self.service.create('opencode')
        first['thread'] = second['thread'] = 'same-native-id'
        self.service.event({'kind': 'item', 'session_id': second['id'], 'thread': 'same-native-id', 'turn': 't',
                            'item': {'id': 'p', 'kind': 'text', 'text': 'second only', 'status': 'completed'}})
        self.assertEqual(first['items'], [])
        self.assertEqual(second['items'][0]['text'], 'second only')

    async def test_catalogs_follow_the_conversation_workspace(self):
        """Another project's picker cannot replace a conversation's allowed model set."""
        first_root = Path(self.temp.name) / 'first'; first_root.mkdir()
        second_root = Path(self.temp.name) / 'second'; second_root.mkdir()

        async def models(workspace=None, refresh=False):
            """Provide distinct native models in the two project configurations."""
            return [{'id': Path(workspace).name + '/model', 'efforts': ['default']}]

        self.service.backends['opencode'].models.side_effect = models
        self.service.projects.state['current'] = {'path': str(first_root)}
        first = self.service.create('opencode')
        self.service.projects.state['current'] = {'path': str(second_root)}
        second = self.service.create('opencode')
        for owner, expected in [(first, 'first/model'), (second, 'second/model')]:
            response = await self.client.get('/api/chat/backends', params={'session_id': owner['id']})
            row = next(r for r in response.json() if r['id'] == 'opencode')
            self.assertEqual(row['models'][0]['id'], expected)
        # Queue in an already-running fixture so no native model turn is launched.
        first['status'] = 'running'
        endpoint = '/api/chat/sessions/' + first['id'] + '/messages'
        wrong = await self.client.post(endpoint, json={'text': 'hello', 'model': 'second/model', 'effort': 'default'})
        self.assertEqual(wrong.status_code, 400)
        valid = await self.client.post(endpoint, json={'text': 'hello', 'model': 'first/model', 'effort': 'default'})
        self.assertEqual(valid.status_code, 202)
        self.assertEqual(len(first['queue']), 1)
        self.assertEqual((await self.client.get('/api/chat/backends?session_id=missing')).status_code, 404)

    async def test_catalog_disconnect_does_not_stop_conversations(self):
        """Read-only catalog processes do not own or terminate a model execution."""
        owner = self.service.create('opencode'); owner['status'] = 'running'
        event = {'kind': 'disconnected', 'error': 'catalog connection lost'}
        self.service.backends['opencode'].emit(event)
        self.assertEqual(owner['status'], 'running')
        self.service.event({**event, 'session_id': owner['id']})
        self.assertEqual(owner['status'], 'error')

    async def test_execution_disconnect_settles_items_and_pauses_queue(self):
        """A lost execution process checkpoints partial output and never advances its queue."""
        owners = [self.service.create(name) for name in ('codex', 'opencode')]
        for owner in owners:
            owner.update(status='running', thread='native-' + owner['backend'], turn='active-turn',
                         queue=[{'message': {'text': 'next request'}, 'tier': None}])
            owner['items'] = [
                {'id': 'tool', 'kind': 'tool', 'text': 'partial output', 'status': 'running'},
                {'id': 'reply', 'kind': 'text', 'text': 'partial reply', 'status': 'inProgress'},
                {'id': 'finished', 'kind': 'tool', 'text': 'previous result', 'status': 'completed', 'ended_at': 10},
            ]
            self.service.catalog_reconnect.add(owner['id'])
        self.service.event({'kind': 'disconnected', 'session_id': owners[0]['id'], 'error': 'process exited'})
        self.assertEqual(owners[1]['status'], 'running')
        self.service.event({'kind': 'disconnected', 'session_id': owners[1]['id'], 'error': 'process exited'})
        for owner in owners:
            with self.subTest(backend=owner['backend']):
                saved = json.loads((self.service.root / (owner['id'] + '.json')).read_text())
                self.assertEqual(saved['status'], 'error')
                self.assertEqual(saved['error'], 'process exited')
                self.assertTrue(saved['queue_paused'])
                self.assertEqual(len(saved['queue']), 1)
                self.assertEqual([row['status'] for row in saved['items']], ['error', 'error', 'completed'])
                self.assertEqual([row['text'] for row in saved['items']], ['partial output', 'partial reply', 'previous result'])
                self.assertTrue(all(row.get('ended_at') for row in saved['items']))
                self.assertEqual(saved['items'][-1]['ended_at'], 10)
                self.assertNotIn(owner['id'], self.service.catalog_reconnect)
        flusher = asyncio.create_task(self.service.flush())
        try:
            with patch.object(self.service, 'launch') as launch:
                await asyncio.sleep(1.05)
                launch.assert_not_called()
                self.assertTrue(all(len(owner['queue']) == 1 for owner in owners))
        finally:
            flusher.cancel()
            await asyncio.gather(flusher, return_exceptions=True)

    async def test_explicit_reconnect_preserves_history_and_waits_for_tasks(self):
        """Refresh an idle execution client, rejecting both active parents and children."""
        owner = self.service.create('opencode'); owner['thread'] = 'native-history'
        owner['items'] = [{'id': 'existing', 'kind': 'text', 'text': 'Keep this history'}]
        backend = SimpleNamespace(close=AsyncMock(), emit=lambda e: None)
        self.service.session_backends[owner['id']] = backend
        endpoint = '/api/chat/sessions/' + owner['id'] + '/reconnect'
        owner['status'] = 'running'
        self.assertEqual((await self.client.post(endpoint, json={})).status_code, 409)
        owner['status'] = 'completed'; owner['children_running'] = 1
        self.assertEqual((await self.client.post(endpoint, json={})).status_code, 409)
        backend.close.assert_not_awaited()
        owner['children_running'] = 0
        self.assertEqual((await self.client.post(endpoint, json={})).status_code, 200)
        backend.close.assert_awaited_once()
        self.assertNotIn(owner['id'], self.service.session_backends)
        self.assertEqual(owner['thread'], 'native-history')
        self.assertEqual(owner['items'][0]['text'], 'Keep this history')

    async def test_service_shutdown_checkpoints_interrupted_tasks_and_close_errors(self):
        """Shutdown settles partial blocks, preserves finished results and pauses queued work."""
        owners = [self.service.create(name) for name in ('codex', 'opencode')]
        for owner in owners:
            owner.update(status='running', thread='native-' + owner['backend'], turn='active', started_at=10,
                         queue=[{'message': {'text': 'queued'}, 'tier': None}])
            owner['items'] = [
                {'id': 'request', 'kind': 'user', 'text': 'task', 'status': 'completed', 'task_card': {'title': 'Task'}},
                {'id': 'partial', 'kind': 'tool', 'text': 'partial output', 'status': 'running'},
                {'id': 'finished', 'kind': 'text', 'text': 'existing result', 'status': 'completed', 'ended_at': 12},
            ]
            backend = SimpleNamespace(emit=lambda event: None, close=AsyncMock())
            if owner['backend'] == 'opencode':
                backend.close.side_effect = RuntimeError('Native command cancellation could not be confirmed')
            self.service.session_backends[owner['id']] = backend
        await self.service.close()
        for owner in owners:
            expected = 'error' if owner['backend'] == 'opencode' else 'interrupted'
            saved = json.loads((self.service.root / (owner['id'] + '.json')).read_text())
            self.assertEqual(saved['status'], expected)
            self.assertTrue(saved['queue_paused'])
            self.assertTrue(saved['ended_at'])
            self.assertEqual(len(saved['queue']), 1)
            self.assertEqual(saved['items'][0]['task_run']['status'], expected)
            self.assertEqual(saved['items'][1]['status'], expected)
            self.assertEqual(saved['items'][1]['text'], 'partial output')
            self.assertEqual(saved['items'][2]['ended_at'], 12)
            self.assertTrue(saved['delegation_stopped'])
        # Repeated close during test teardown is already idle and remains harmless.
        self.service.session_backends.clear()

    async def test_terminal_turn_survives_cancelled_send_wait(self):
        """Native terminal events remain authoritative when shutdown cancels the RPC waiter."""
        for status in ('completed', 'failed', 'interrupted'):
            with self.subTest(status=status):
                owner = self.service.create('opencode')
                entered = asyncio.Event()

                async def send(*args):
                    """Deliver a real protocol boundary before the send response returns."""
                    self.service.event({'kind': 'turn', 'thread': owner['thread'], 'turn': 'turn',
                                        'status': status, 'error': 'native failure' if status == 'failed' else ''})
                    entered.set()
                    await asyncio.Future()

                backend = SimpleNamespace(open=AsyncMock(return_value='thread-' + status),
                    send=AsyncMock(side_effect=send), close=AsyncMock(), emit=lambda e: None)
                self.service.session_backends[owner['id']] = backend
                response = await self.client.post('/api/chat/sessions/' + owner['id'] + '/messages', json={
                    'text': 'task', 'model': 'provider/model', 'effort': 'default',
                    'task_card': {'title': 'Task', 'workflow': '', 'project': 'fixture'}})
                self.assertEqual(response.status_code, 202)
                await asyncio.wait_for(entered.wait(), 2)
                await self.service.close()
                expected = 'error' if status == 'failed' else status
                saved = json.loads((self.service.root / (owner['id'] + '.json')).read_text())
                self.assertEqual(saved['status'], expected)
                self.assertEqual(saved['items'][0]['task_run']['status'], expected)
                self.assertTrue(saved['queue_paused'])

    async def test_terminal_turn_survives_late_send_error(self):
        """A transport failure after native completion must not replace the recorded result."""
        owner = self.service.create('codex')

        async def send(*args):
            """Finish the native turn before its separate send acknowledgment fails."""
            self.service.event({'kind': 'turn', 'thread': owner['thread'], 'turn': 'turn',
                                'status': 'completed', 'error': ''})
            raise RuntimeError('send acknowledgment lost')

        self.service.session_backends[owner['id']] = SimpleNamespace(open=AsyncMock(return_value='native-history'),
            send=AsyncMock(side_effect=send), close=AsyncMock(), emit=lambda e: None)
        response = await self.client.post('/api/chat/sessions/' + owner['id'] + '/messages', json={
            'text': 'task', 'model': 'codex-model', 'effort': 'low'})
        self.assertEqual(response.status_code, 202)
        await asyncio.gather(*self.service.jobs)
        saved = json.loads((self.service.root / (owner['id'] + '.json')).read_text())
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['error'], '')
        self.assertTrue(saved['queue_paused'])

    async def test_new_prompt_waits_for_pending_guidance_receipt(self):
        """A terminal native event cannot let a new request race unfinished guidance delivery."""
        owner = self.service.create('opencode'); owner['status'] = 'completed'
        self.service.steering.add(owner['id'])
        self.service.run = AsyncMock()
        try:
            response = await self.client.post('/api/chat/sessions/' + owner['id'] + '/messages', json={
                'text': 'next request', 'model': 'provider/model', 'effort': 'default'})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(len(owner.get('queue', [])), 1)
            self.service.run.assert_not_awaited()
        finally:
            self.service.steering.discard(owner['id'])

    async def test_catalog_handoff_remains_owned_until_continuation_is_scheduled(self):
        """A slow client close must not expose a finished task or orphan its process."""
        owner = self.service.create('opencode')
        owner.update(status='completed', model='provider/model', effort='default',
                     workflow_runs=[{'id': 'card', 'status': 'running'}])
        closing, release = asyncio.Event(), asyncio.Event()

        async def close():
            """Hold the native client shutdown at an observable asynchronous boundary."""
            closing.set()
            await release.wait()

        backend = SimpleNamespace(close=AsyncMock(side_effect=close), emit=lambda event: None)
        self.service.session_backends[owner['id']] = backend
        self.service.workflow_bridges[owner['id']] = SimpleNamespace(
            active_card='card', runtime=SimpleNamespace(inspect=AsyncMock()), close=AsyncMock())
        self.service.catalog_reconnect.add(owner['id'])
        self.service.run = AsyncMock()
        self.service.flusher = asyncio.create_task(self.service.flush())
        try:
            await asyncio.wait_for(closing.wait(), 3)
            self.assertIn(owner['id'], self.service.catalog_reconnect)
            self.assertIs(self.service.session_backends[owner['id']], backend)
            self.service.run.assert_not_awaited()
            release.set()
            async with asyncio.timeout(3):
                while not self.service.run.await_count:
                    await asyncio.sleep(.01)
            self.assertNotIn(owner['id'], self.service.catalog_reconnect)
            self.assertNotIn(owner['id'], self.service.session_backends)
            self.assertEqual(owner['status'], 'starting')
            self.assertIn(owner['id'], self.service.launching)
        finally:
            release.set()

    async def test_catalog_handoff_preserves_live_card_but_not_stopped_tasks(self):
        """Internal reconnection preserves activity; user stop and host exit still pause."""
        owner = self.service.create('opencode')
        owner.update(status='completed', delegation_stopped=False, children_pending=False)
        root = Path(self.temp.name) / 'card'; root.mkdir()
        card = {'id': 'card', 'root': str(root), 'status': 'running'}
        owner['workflow_runs'] = [card]
        runtime = WorkflowRuntime(self.service, owner)
        runtime.processes['card'] = SimpleNamespace(returncode=None)
        self.service.catalog_reconnect.add(owner['id'])
        await runtime.inspect()
        self.assertEqual(card['status'], 'running')
        self.assertTrue(card['alive'])
        # The next native turn takes ownership before the reconnect marker clears.
        owner['status'] = 'starting'
        self.service.catalog_reconnect.discard(owner['id'])
        await runtime.inspect()
        self.assertEqual(card['status'], 'running')
        owner['status'] = 'completed'
        await runtime.inspect()
        self.assertEqual(card['status'], 'paused')
        card['status'] = 'running'
        self.service.catalog_reconnect.add(owner['id'])
        owner['delegation_stopped'] = True
        await runtime.inspect()
        self.assertEqual(card['status'], 'paused')
        owner['delegation_stopped'] = False
        owner['permission_mode'] = 'readonly'; card['status'] = 'running'
        await runtime.inspect()
        self.assertEqual(card['status'], 'paused')
        owner['permission_mode'] = 'workflow'
        card['status'] = 'running'; runtime.processes['card'].returncode = 1
        await runtime.inspect()
        self.assertEqual(card['status'], 'paused')
        self.assertFalse(card['alive'])

    async def test_stop_during_catalog_handoff_cancels_continuation(self):
        """A stop arriving during native shutdown must win over automatic resumption."""
        owner = self.service.create('opencode')
        owner.update(status='completed', model='provider/model', effort='default',
                     delegation_stopped=False, workflow_runs=[{'id': 'card', 'status': 'running'}])
        closing, release = asyncio.Event(), asyncio.Event()

        async def close():
            """Allow the user stop request to interleave with backend shutdown."""
            closing.set()
            await release.wait()

        backend = SimpleNamespace(close=AsyncMock(side_effect=close), emit=lambda event: None)
        self.service.session_backends[owner['id']] = backend
        self.service.workflow_bridges[owner['id']] = SimpleNamespace(active_card='card', call=None,
            runtime=SimpleNamespace(inspect=AsyncMock(), pause=AsyncMock()), close=AsyncMock())
        self.service.catalog_reconnect.add(owner['id'])
        self.service.run = AsyncMock()
        self.service.flusher = asyncio.create_task(self.service.flush())
        try:
            await asyncio.wait_for(closing.wait(), 3)
            self.assertIn(owner['id'], self.service.launching)
            response = await self.client.post('/api/chat/sessions/' + owner['id'] + '/stop')
            self.assertEqual(response.status_code, 200)
            release.set()
            async with asyncio.timeout(3):
                while owner['id'] in self.service.launching:
                    await asyncio.sleep(.01)
            self.service.run.assert_not_awaited()
            self.assertTrue(owner['queue_paused'])
            self.assertTrue(owner['delegation_stopped'])
            self.assertNotIn(owner['id'], self.service.catalog_reconnect)
        finally:
            release.set()

    async def test_stop_rejects_new_guidance_before_workflow_pause_returns(self):
        """The stop boundary is visible before awaiting workflow or descendant cleanup."""
        for name in ('codex', 'opencode'):
            with self.subTest(backend=name):
                owner = self.service.create(name)
                owner.update(status='running', thread='native-' + name, turn='turn',
                             queue=[{'id': 'queued', 'message': {'text': 'guide', 'model': 'provider/model',
                                                               'effort': 'default'}, 'tier': None}])
                entered, release = asyncio.Event(), asyncio.Event()

                async def pause():
                    """Keep the stop request waiting on existing workflow cleanup."""
                    entered.set(); await release.wait()

                backend = SimpleNamespace(steer=AsyncMock(), interrupt=AsyncMock(),
                    interrupt_descendants=AsyncMock(), close=AsyncMock(), emit=lambda event: None)
                self.service.session_backends[owner['id']] = backend
                self.service.workflow_bridges[owner['id']] = SimpleNamespace(call=None,
                    runtime=SimpleNamespace(pause=pause), close=AsyncMock())
                endpoint = '/api/chat/sessions/' + owner['id']
                stopping = asyncio.create_task(self.client.post(endpoint + '/stop'))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    response = await self.client.post(endpoint + '/queue/queued/steer')
                    self.assertEqual(response.status_code, 409)
                    backend.steer.assert_not_awaited()
                    self.assertEqual(len(owner['queue']), 1)
                    self.assertTrue(owner['queue_paused'])
                    release.set()
                    self.assertEqual((await stopping).status_code, 200)
                finally:
                    release.set(); await asyncio.gather(stopping, return_exceptions=True)

    async def test_stop_attempts_all_cleanup_and_preserves_failures(self):
        """One failed cleanup cannot skip another owner or hide uncertain cancellation."""
        for name in ('codex', 'opencode'):
            for faults in ({'workflow'}, {'children'}, {'parent'}, {'workflow', 'children', 'parent'}):
                with self.subTest(backend=name, faults=sorted(faults)):
                    owner = self.service.create(name)
                    owner.update(status='running', thread='native-' + owner['id'], turn='turn',
                        started_at=1, queue=[{'id': 'queued', 'message': {'text': 'queued'}, 'tier': None}], items=[
                            {'id': 'user', 'kind': 'user', 'text': 'task', 'status': 'completed',
                             'task_card': {'title': 'Task'}, 'started_at': 1},
                            {'id': 'partial', 'kind': 'text', 'text': 'Keep partial', 'status': 'running'}])
                    backend = SimpleNamespace(interrupt_descendants=AsyncMock(
                        side_effect=RuntimeError('children cleanup failed') if 'children' in faults else None),
                        interrupt=AsyncMock(side_effect=RuntimeError('parent cleanup failed') if 'parent' in faults else None),
                        close=AsyncMock(), emit=lambda event: None)
                    bridge = SimpleNamespace(runtime=SimpleNamespace(pause=AsyncMock(
                        side_effect=RuntimeError('workflow cleanup failed') if 'workflow' in faults else None)),
                        call=SimpleNamespace(cancel=unittest.mock.Mock()), close=AsyncMock())
                    self.service.session_backends[owner['id']] = backend
                    self.service.workflow_bridges[owner['id']] = bridge
                    endpoint = '/api/chat/sessions/' + owner['id']
                    response = await self.client.post(endpoint + '/stop')
                    self.assertEqual(response.status_code, 502)
                    bridge.runtime.pause.assert_awaited_once()
                    bridge.call.cancel.assert_called_once()
                    backend.interrupt_descendants.assert_awaited_once_with(owner['thread'])
                    backend.interrupt.assert_awaited_once_with(owner['thread'], 'turn')
                    for fault in faults:
                        self.assertIn(fault + ' cleanup failed', response.json()['detail'])
                    for status in ('inProgress', 'interrupted', 'completed'):
                        self.service.event({'kind': 'turn', 'session_id': owner['id'],
                            'thread': owner['thread'], 'turn': 'turn', 'status': status, 'error': ''})
                        self.assertEqual(owner['status'], 'error')
                    saved = json.loads((self.service.root / (owner['id'] + '.json')).read_text())
                    self.assertEqual(saved['status'], 'error')
                    self.assertEqual(saved['items'][0]['task_run']['status'], 'error')
                    self.assertEqual(saved['items'][1]['status'], 'error')
                    self.assertEqual(saved['items'][1]['text'], 'Keep partial')
                    self.assertTrue(saved['queue_paused'])
                    self.assertEqual(len(saved['queue']), 1)
                    for fault in faults:
                        self.assertIn(fault + ' cleanup failed', saved['error'])
                    self.assertEqual((await self.client.post(endpoint + '/queue/queued/remove')).status_code, 200)
                    with patch.object(self.service, 'run', new=AsyncMock()):
                        response = await self.client.post(endpoint + '/messages', json={
                            'text': 'new explicit task', 'model': 'provider/model' if name == 'opencode' else 'codex-model',
                            'effort': 'default' if name == 'opencode' else 'low'})
                        self.assertEqual(response.status_code, 202)
                        await asyncio.gather(*self.service.jobs)
                    self.assertNotIn('stop_error', owner)
                    self.assertFalse(owner['delegation_stopped'])
                    self.assertFalse(owner['error'])
                    self.service.event({'kind': 'turn', 'session_id': owner['id'],
                        'thread': owner['thread'], 'turn': 'next', 'status': 'completed', 'error': ''})

    async def test_descendant_failure_does_not_skip_remaining_children(self):
        """Attempt the other known native children when one lookup or abort fails."""
        for name, fault in (('opencode', 'abort'), ('codex', 'query'), ('codex', 'abort')):
            with self.subTest(backend=name, fault=fault):
                backend = self.service.backends[name]
                backend.agents = AsyncMock(return_value={'agents': [
                    {'id': 'failed-child', 'status': 'running'},
                    {'id': 'live-child', 'status': 'running'},
                    {'id': 'idle-child', 'status': 'idle'}]})
                cancelled = []

                async def request(method, params):
                    """Supply native turn state and fail exactly one child's selected boundary."""
                    identity = params['threadId']
                    if method == 'thread/turns/list':
                        if fault == 'query' and identity == 'failed-child':
                            raise RuntimeError('child query failed')
                        return {'data': [{'id': 'turn', 'status': 'completed' if identity == 'idle-child' else 'inProgress'}]}
                    if identity == 'failed-child':
                        raise RuntimeError('child abort failed')
                    cancelled.append(identity)

                async def http(method, path, body):
                    """Fail one HTTP abort without changing the remaining sessions."""
                    identity = path.split('/')[2]
                    if identity == 'failed-child':
                        raise RuntimeError('child abort failed')
                    cancelled.append(identity)

                backend.request, backend.http = AsyncMock(side_effect=request), AsyncMock(side_effect=http)
                with self.assertRaisesRegex(RuntimeError, 'failed-child'):
                    await backend.interrupt_descendants('parent')
                self.assertEqual(cancelled, ['live-child'])

    async def test_failed_stop_during_open_cannot_start_generation(self):
        """A cleanup error must not erase the stop boundary while opening a native thread."""
        owner = self.service.create('opencode')
        entered, release = asyncio.Event(), asyncio.Event()

        async def opening(*args):
            """Delay native readiness until the stop request has reported its cleanup error."""
            entered.set(); await release.wait()
            return 'native-opened'

        backend = SimpleNamespace(open=AsyncMock(side_effect=opening), send=AsyncMock(),
            interrupt=AsyncMock(), interrupt_descendants=AsyncMock(), close=AsyncMock(), emit=lambda event: None)
        bridge = SimpleNamespace(call=None, runtime=SimpleNamespace(pause=AsyncMock(
            side_effect=RuntimeError('workflow cleanup failed'))), close=AsyncMock())
        self.service.session_backends[owner['id']] = backend
        self.service.workflow_bridges[owner['id']] = bridge
        endpoint = '/api/chat/sessions/' + owner['id']
        try:
            self.assertEqual((await self.client.post(endpoint + '/messages', json={
                'text': 'task', 'model': 'provider/model', 'effort': 'default'})).status_code, 202)
            await asyncio.wait_for(entered.wait(), 2)
            self.assertEqual((await self.client.post(endpoint + '/stop')).status_code, 502)
            release.set()
            await asyncio.wait_for(asyncio.gather(*self.service.jobs), 2)
            backend.send.assert_not_awaited()
            self.assertEqual(owner['status'], 'error')
            self.assertIn('workflow cleanup failed', owner['error'])
            self.assertTrue(owner['queue_paused'])
        finally:
            release.set()

    async def test_manual_reconnect_queues_new_messages_until_closed(self):
        """A new prompt cannot reopen the same client while refresh is shutting it down."""
        owner = self.service.create('opencode'); owner['status'] = 'completed'
        endpoint = '/api/chat/sessions/' + owner['id']
        closing, release = asyncio.Event(), asyncio.Event()

        async def close():
            """Hold refresh while a second request attempts to start work."""
            closing.set()
            await release.wait()

        backend = SimpleNamespace(close=AsyncMock(side_effect=close), emit=lambda event: None)
        self.service.session_backends[owner['id']] = backend
        self.service.run = AsyncMock()
        refresh = asyncio.create_task(self.client.post(endpoint + '/reconnect'))
        try:
            await asyncio.wait_for(closing.wait(), 3)
            self.assertIs(self.service.session_backends[owner['id']], backend)
            self.assertEqual((await self.client.post(endpoint + '/reconnect')).status_code, 409)
            response = await self.client.post(endpoint + '/messages', json={
                'text': 'after refresh', 'model': 'provider/model', 'effort': 'default'})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(len(owner['queue']), 1)
            self.service.run.assert_not_awaited()
            release.set()
            self.assertEqual((await refresh).status_code, 200)
            self.assertNotIn(owner['id'], self.service.launching)
            self.assertNotIn(owner['id'], self.service.session_backends)
        finally:
            release.set()
            await refresh

    async def test_policy_and_attachment_changes_own_connections_until_commit(self):
        """New messages wait for the complete policy/MCP transition and use its final state."""
        for operation in ('permissions', 'attachment', 'attachment/remove'):
            with self.subTest(operation=operation):
                owner = self.service.create('opencode')
                owner.update(status='completed', thread='native-history', permission_mode='full',
                             attachment={'workflow_id': 'original'}, workflow_runs=[{'id': 'card', 'workflow_id': 'original', 'authorized': True}])
                endpoint = '/api/chat/sessions/' + owner['id']
                closing, release, bridge_closing, release_bridge = (asyncio.Event() for _ in range(4))

                async def close():
                    """Hold the old native connection during a concurrent HTTP request."""
                    closing.set(); await release.wait()

                async def close_bridge():
                    """Keep the transition open until the old workflow endpoint also closes."""
                    bridge_closing.set(); await release_bridge.wait()

                backend = SimpleNamespace(close=AsyncMock(side_effect=close), emit=lambda event: None)
                bridge = SimpleNamespace(close=AsyncMock(side_effect=close_bridge))
                self.service.session_backends[owner['id']] = backend
                self.service.workflow_bridges[owner['id']] = bridge
                self.service.run = AsyncMock()
                payload = {'mode': 'readonly'} if operation == 'permissions' else {'workflow_id': 'replacement'}
                with patch('flowcanvas.chat.attachment', return_value={'workflow_id': 'replacement'}):
                    changing = asyncio.create_task(self.client.post(endpoint + '/' + operation, json=payload))
                    try:
                        await asyncio.wait_for(closing.wait(), 2)
                        self.assertIs(self.service.session_backends.get(owner['id']), backend)
                        self.assertIn(owner['id'], self.service.launching)
                        competing = await self.client.post(endpoint + '/permissions', json={'mode': 'workflow'})
                        self.assertEqual(competing.status_code, 409)
                        response = await self.client.post(endpoint + '/messages', json={
                            'text': 'after transition', 'model': 'provider/model', 'effort': 'default'})
                        self.assertEqual(response.status_code, 202)
                        self.assertEqual(len(owner['queue']), 1)
                        self.service.run.assert_not_awaited()
                        self.assertEqual(owner['permission_mode'], 'full')
                        self.assertEqual(owner['attachment']['workflow_id'], 'original')
                        release.set()
                        if operation != 'permissions':
                            await asyncio.wait_for(bridge_closing.wait(), 2)
                            self.assertIs(self.service.workflow_bridges.get(owner['id']), bridge)
                            self.assertIn(owner['id'], self.service.launching)
                            self.service.run.assert_not_awaited()
                            release_bridge.set()
                        self.assertEqual((await changing).status_code, 200)
                        self.assertNotIn(owner['id'], self.service.launching)
                        self.assertNotIn(owner['id'], self.service.session_backends)
                        if operation == 'permissions':
                            self.assertEqual(owner['permission_mode'], 'readonly')
                            self.assertIs(self.service.workflow_bridges[owner['id']], bridge)
                        elif operation == 'attachment':
                            self.assertEqual(owner['attachment']['workflow_id'], 'replacement')
                        else:
                            self.assertNotIn('attachment', owner)
                            self.assertFalse(owner['workflow_runs'][0]['authorized'])
                    finally:
                        release.set(); release_bridge.set()
                        await asyncio.gather(changing, return_exceptions=True)

    async def test_failed_policy_change_preserves_mode_and_pauses_queued_requests(self):
        """A failed close cannot leave an accepted queued request running under the old grant."""
        owner = self.service.create('opencode')
        owner.update(status='completed', permission_mode='full')
        endpoint = '/api/chat/sessions/' + owner['id']
        closing, release = asyncio.Event(), asyncio.Event()

        async def close():
            """Fail only after a message has arrived during the pending policy change."""
            closing.set(); await release.wait()
            raise RuntimeError('connection close failed')

        emit = lambda event: None
        backend = SimpleNamespace(close=AsyncMock(side_effect=close), emit=emit)
        self.service.session_backends[owner['id']] = backend
        self.service.run = AsyncMock()
        changing = asyncio.create_task(self.client.post(endpoint + '/permissions', json={'mode': 'readonly'}))
        try:
            await asyncio.wait_for(closing.wait(), 2)
            response = await self.client.post(endpoint + '/messages', json={
                'text': 'must wait', 'model': 'provider/model', 'effort': 'default'})
            self.assertEqual(response.status_code, 202)
            release.set()
            self.assertEqual((await changing).status_code, 502)
            self.assertEqual(owner['permission_mode'], 'full')
            self.assertTrue(owner['queue_paused'])
            self.assertEqual(len(owner['queue']), 1)
            self.assertIs(self.service.session_backends[owner['id']], backend)
            self.assertIs(backend.emit, emit)
            self.assertNotIn(owner['id'], self.service.launching)
            self.service.run.assert_not_awaited()
        finally:
            release.set()
            await asyncio.gather(changing, return_exceptions=True)
            self.service.session_backends.pop(owner['id'], None)

    async def test_deleting_conversation_rejects_sends_during_close(self):
        """Deleting a session must not acknowledge a new prompt that it will discard."""
        owner = self.service.create('opencode'); owner['status'] = 'completed'
        endpoint = '/api/chat/sessions/' + owner['id']
        closing, release = asyncio.Event(), asyncio.Event()

        async def close():
            """Hold deletion before its resources and stored state are removed."""
            closing.set(); await release.wait()

        self.service.session_backends[owner['id']] = SimpleNamespace(close=AsyncMock(side_effect=close), emit=lambda event: None)
        self.service.run = AsyncMock()
        deleting = asyncio.create_task(self.client.post(endpoint + '/delete'))
        try:
            await asyncio.wait_for(closing.wait(), 2)
            response = await self.client.post(endpoint + '/messages', json={
                'text': 'do not discard silently', 'model': 'provider/model', 'effort': 'default'})
            self.assertEqual(response.status_code, 409)
            self.service.run.assert_not_awaited()
            release.set()
            self.assertEqual((await deleting).status_code, 200)
            self.assertNotIn(owner['id'], self.service.sessions)
        finally:
            release.set(); await asyncio.gather(deleting, return_exceptions=True)

    async def test_agent_observers_wait_for_connection_replacement(self):
        """A sidebar poll must never reconnect a retired execution client during closure."""
        for name in ('codex', 'opencode'):
            with self.subTest(backend=name):
                owner = self.service.create(name)
                owner.update(thread='owned-history', status='completed')
                endpoint = '/api/chat/sessions/' + owner['id']
                entered, release = asyncio.Event(), asyncio.Event()

                async def close():
                    """Expose the window after native shutdown but before ownership release."""
                    entered.set(); await release.wait()

                result = {'agents': [], 'selected': None, 'items': []}
                backend = SimpleNamespace(close=AsyncMock(side_effect=close), emit=lambda e: None,
                                          agents=AsyncMock(return_value=result))
                self.service.session_backends[owner['id']] = backend
                self.service.backends[name].agents = AsyncMock(return_value=result)
                changing = asyncio.create_task(self.client.post(endpoint + '/reconnect'))
                poll = None
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    poll = asyncio.create_task(self.client.get(endpoint + '/subagents'))
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(poll), .05)
                    backend.agents.assert_not_awaited()
                    release.set()
                    self.assertEqual((await changing).status_code, 200)
                    self.assertEqual((await poll).status_code, 200)
                    backend.agents.assert_not_awaited()
                    self.service.backends[name].agents.assert_awaited_once_with('owned-history', None, True)
                finally:
                    release.set()
                    await asyncio.gather(changing, *([poll] if poll else []), return_exceptions=True)

    async def test_connection_replacement_waits_for_existing_agent_observers(self):
        """An already-started observer finishes before its native connection is reaped."""
        owner = self.service.create('opencode')
        owner.update(thread='owned-history', status='completed')
        endpoint = '/api/chat/sessions/' + owner['id']
        entered, release = asyncio.Event(), asyncio.Event()

        async def agents(*args):
            """Pause while holding a real history read that may still need more requests."""
            entered.set(); await release.wait()
            return {'agents': [], 'selected': None, 'items': []}

        backend = SimpleNamespace(close=AsyncMock(), emit=lambda e: None, agents=AsyncMock(side_effect=agents))
        self.service.session_backends[owner['id']] = backend
        poll = asyncio.create_task(self.client.get(endpoint + '/subagents'))
        changing = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            changing = asyncio.create_task(self.client.post(endpoint + '/reconnect'))
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(changing), .05)
            backend.close.assert_not_awaited()
            release.set()
            self.assertEqual((await poll).status_code, 200)
            self.assertEqual((await changing).status_code, 200)
            backend.close.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(poll, *([changing] if changing else []), return_exceptions=True)

    async def test_agent_observers_respect_deletion_and_shutdown(self):
        """Waiting readers cannot revive a deleted conversation or a shutting-down service."""
        owner = self.service.create('opencode')
        owner.update(thread='owned-history', status='completed')
        endpoint = '/api/chat/sessions/' + owner['id']
        entered, release = asyncio.Event(), asyncio.Event()

        async def close():
            """Keep deletion open so a new reader waits on the same owner."""
            entered.set(); await release.wait()

        backend = SimpleNamespace(close=AsyncMock(side_effect=close), emit=lambda e: None, agents=AsyncMock())
        self.service.session_backends[owner['id']] = backend
        self.service.backends['opencode'].agents = AsyncMock()
        deleting = asyncio.create_task(self.client.post(endpoint + '/delete'))
        poll = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            poll = asyncio.create_task(self.client.get(endpoint + '/subagents'))
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(poll), .05)
            release.set()
            self.assertEqual((await deleting).status_code, 200)
            self.assertEqual((await poll).status_code, 404)
            backend.agents.assert_not_awaited()
            self.service.backends['opencode'].agents.assert_not_awaited()
        finally:
            release.set()
            await asyncio.gather(deleting, *([poll] if poll else []), return_exceptions=True)
        survivor = self.service.create('opencode'); survivor['thread'] = 'another-history'
        entered.clear(); release.clear()

        async def agents(*args):
            """Keep a pre-shutdown HTTP observation alive while the service begins closing."""
            entered.set(); await release.wait()
            return {'agents': [], 'selected': None, 'items': []}

        backend = SimpleNamespace(close=AsyncMock(), emit=lambda e: None, agents=AsyncMock(side_effect=agents))
        self.service.session_backends[survivor['id']] = backend
        endpoint = '/api/chat/sessions/' + survivor['id'] + '/subagents'
        poll = asyncio.create_task(self.client.get(endpoint))
        shutdown = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            shutdown = asyncio.create_task(self.service.close())
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(shutdown), .05)
            backend.close.assert_not_awaited()
            release.set()
            self.assertEqual((await poll).status_code, 200)
            await shutdown
            backend.close.assert_awaited_once()
            self.assertEqual((await self.client.get(endpoint)).status_code, 502)
            backend.agents.assert_awaited_once()
            self.service.backends['opencode'].agents.assert_not_awaited()
        finally:
            release.set()
            await asyncio.gather(poll, *([shutdown] if shutdown else []), return_exceptions=True)

    async def test_model_lookup_cannot_launch_a_deleted_or_reassigned_session(self):
        """A model discovery await is not authorization to use stale conversation ownership."""
        for change in ('delete', 'backend'):
            with self.subTest(change=change):
                owner = self.service.create('opencode')
                endpoint = '/api/chat/sessions/' + owner['id']
                entered, release = asyncio.Event(), asyncio.Event()

                async def models(*args, **kwargs):
                    """Delay catalog discovery while another request changes session ownership."""
                    entered.set(); await release.wait()
                    return [{'id': 'provider/model', 'efforts': ['default']}]

                self.service.run = AsyncMock()
                with patch.object(self.service, 'models', side_effect=models):
                    sending = asyncio.create_task(self.client.post(endpoint + '/messages', json={
                        'text': 'stale request', 'model': 'provider/model', 'effort': 'default'}))
                    try:
                        await asyncio.wait_for(entered.wait(), 2)
                        response = await self.client.post(endpoint + '/' + change, json={'backend': 'codex'})
                        self.assertEqual(response.status_code, 200)
                        release.set()
                        self.assertEqual((await sending).status_code, 404 if change == 'delete' else 409)
                        self.service.run.assert_not_awaited()
                    finally:
                        release.set(); await asyncio.gather(sending, return_exceptions=True)


class OpenCodeEventTests(unittest.IsolatedAsyncioTestCase):
    """Verify streaming snapshots, cancellation and stale idle handling deterministically."""

    async def asyncSetUp(self):
        """Create an adapter with fake HTTP and a single active native turn."""
        self.events = []
        self.backend = OpenCodeBackend(Path('/tmp'), self.events.append)
        self.backend.active['s'] = 'u'
        self.backend.messages['a'] = {'role': 'assistant', 'parentID': 'u'}
        self.backend.http = AsyncMock()

    async def test_catalog_reload_waits_for_a_safe_turn_boundary(self):
        """Do not replace clients referenced by active tools or resurrect a closing connection."""
        self.backend.mcp_url = 'http://127.0.0.1:1/mcp'
        self.backend.http.return_value = {'ucagent': {'status': 'connected'}}
        await self.backend.reload_tools()
        self.backend.http.assert_not_awaited()
        self.backend.active.clear()
        await self.backend.reload_tools()
        self.backend.http.assert_awaited_once()
        self.assertEqual(self.backend.http.call_args.args[:2], ('POST', '/mcp'))
        self.backend.http.reset_mock()
        self.backend.closing = True
        await self.backend.reload_tools()
        self.backend.http.assert_not_awaited()

    async def test_new_prompt_waits_for_an_idle_catalog_refresh(self):
        """An initially idle refresh must finish before a concurrent prompt reaches native execution."""
        self.backend.active.clear()
        self.backend.mcp_url = 'http://127.0.0.1:1/mcp'
        entered, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def http(method, path, body=None):
            """Keep native client replacement pending while a new request is submitted."""
            if path == '/mcp':
                entered.set(); await release.wait()
                return {'ucagent': {'status': 'connected'}}
            if path.endswith('/prompt_async'): delivered.set()

        self.backend.http.side_effect = http
        refreshing = asyncio.create_task(self.backend.reload_tools())
        sending = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            sending = asyncio.create_task(self.backend.send('s', 'next request', 'provider/model', 'default'))
            async with asyncio.timeout(2):
                while not self.backend.active:
                    await asyncio.sleep(.01)
            self.assertFalse(delivered.is_set(), 'Native prompt overtook the in-progress MCP replacement')
            release.set()
            await asyncio.wait_for(asyncio.gather(refreshing, sending), 2)
            self.assertTrue(delivered.is_set())
        finally:
            release.set()
            await asyncio.gather(refreshing, *([sending] if sending else []), return_exceptions=True)

    async def test_close_aborts_owned_turns_before_reaping_server(self):
        """Graceful close cancels commands while HTTP is available and always reaps resources."""
        for failure in (False, True):
            with self.subTest(abort_failure=failure):
                backend = OpenCodeBackend(Path('/tmp'), lambda event: None)
                backend.active['owned'] = 'turn'
                backend.process = SimpleNamespace(returncode=None, terminate=lambda: order.append('terminate'),
                    wait=AsyncMock(), kill=lambda: order.append('kill'))
                backend.client = SimpleNamespace(closed=False, close=AsyncMock(side_effect=lambda: order.append('http-close')))
                order = []

                async def http(method, path, body=None):
                    """Record cancellation ordering without contacting unrelated native sessions."""
                    self.assertEqual((method, path), ('POST', '/session/owned/abort'))
                    order.append('abort')
                    if failure: raise RuntimeError('native endpoint unavailable')

                backend.http = AsyncMock(side_effect=http)
                if failure:
                    with self.assertRaisesRegex(RuntimeError, 'cancellation'):
                        await backend.close()
                else:
                    await backend.close()
                self.assertEqual(order, ['abort', 'http-close', 'terminate'])
                self.assertEqual(backend.active, {})
                backend.process.wait.assert_awaited_once()

    async def test_reconnect_waits_for_retiring_native_process(self):
        """An observer cannot replace process/client ownership during internal cancellation."""
        backend = OpenCodeBackend(Path('/tmp'), lambda event: None)
        entered, release = asyncio.Event(), asyncio.Event()
        old = SimpleNamespace(returncode=None)
        old.terminate = lambda: setattr(old, 'returncode', 0)
        old.kill = old.terminate

        async def delayed_wait():
            """Hold shutdown after process exit is visible but before its owner is released."""
            entered.set(); await release.wait()
            return 0

        old.wait = AsyncMock(side_effect=delayed_wait)
        old_client = SimpleNamespace(closed=False)
        old_client.close = AsyncMock(side_effect=lambda: setattr(old_client, 'closed', True))
        backend.process, backend.client = old, old_client
        backend.ready.set()
        backend.http = AsyncMock(return_value={})
        backend.read = AsyncMock(side_effect=backend.ready.set)
        replacement = SimpleNamespace(returncode=None, wait=AsyncMock(return_value=0),
            stdout=SimpleNamespace(readline=AsyncMock(return_value=b'http://127.0.0.1:8124\n')))
        replacement.terminate = lambda: setattr(replacement, 'returncode', 0)
        replacement.kill = replacement.terminate
        client = SimpleNamespace(closed=False)
        client.close = AsyncMock(side_effect=lambda: setattr(client, 'closed', True))
        reconnecting = None
        with (patch('flowcanvas.backends.opencode.shutil.which', return_value='/test/opencode'),
              patch('flowcanvas.backends.opencode.asyncio.create_subprocess_exec', return_value=replacement) as spawn,
              patch('flowcanvas.backends.opencode.aiohttp.ClientSession', return_value=client)):
            closing = asyncio.create_task(backend.close())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                reconnecting = asyncio.create_task(backend.connect())
                await asyncio.sleep(.02)
                self.assertFalse(reconnecting.done())
                spawn.assert_not_awaited()
                release.set()
                await asyncio.wait_for(asyncio.gather(closing, reconnecting), 2)
                spawn.assert_awaited_once()
                self.assertIs(backend.process, replacement)
                self.assertIs(backend.client, client)
                self.assertFalse(client.closed)
                self.assertTrue(backend.ready.is_set())
            finally:
                release.set()
                await asyncio.gather(closing, *([reconnecting] if reconnecting else []), return_exceptions=True)
                await backend.close()

    async def test_large_native_events_preserve_following_unicode_frames(self):
        """Large tool results must not break SSE reading or lose subsequent split UTF-8."""
        output = 'signal café ✓⚡\n' * 40000
        tool = {'type': 'message.part.updated', 'properties': {'sessionID': 's', 'part': {
            'id': 'large-tool', 'sessionID': 's', 'messageID': 'a', 'type': 'tool', 'tool': 'read',
            'state': {'status': 'completed', 'input': {}, 'output': output}}}}
        following = {'type': 'message.part.updated', 'properties': {'sessionID': 's', 'part': {
            'id': 'following', 'sessionID': 's', 'messageID': 'a', 'type': 'text', 'text': 'after café ✓⚡'}}}
        payload = ('data: ' + json.dumps(tool, ensure_ascii=False) + '\r\n\r\n' +
                   '\r\n'.join('data: ' + line for line in json.dumps(following, ensure_ascii=False, indent=2).splitlines()) +
                   '\r\n\r\n').encode()
        release, shutdown, observed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        connections = []

        async def serve(reader, writer):
            """Send real local HTTP bytes with event lines larger than aiohttp's line limit."""
            connections.append(writer)
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n')
                await writer.drain()
                await release.wait()
                for offset in range(0, len(payload), 4093):
                    writer.write(payload[offset:offset + 4093])
                    await writer.drain()
                await shutdown.wait()
            finally:
                writer.close()
                await writer.wait_closed()

        def emit(event):
            """Observe terminal transport errors and the event following the large result."""
            self.events.append(event)
            if event['kind'] == 'disconnected' or event.get('item', {}).get('id') == 'following':
                observed.set()

        server = await asyncio.start_server(serve, '127.0.0.1', 0)
        self.backend.url = 'http://127.0.0.1:' + str(server.sockets[0].getsockname()[1])
        self.backend.client = aiohttp.ClientSession()
        self.backend.process = SimpleNamespace(returncode=0)
        self.backend.emit = emit
        self.backend.active.clear()
        async with server:
            self.backend.background(self.backend.read())
            try:
                await asyncio.wait_for(self.backend.ready.wait(), 3)
                self.backend.active['s'] = 'u'
                release.set()
                await asyncio.wait_for(observed.wait(), 3)
                blocks = [event['item'] for event in self.events if event['kind'] == 'item']
                self.assertEqual([block['id'] for block in blocks], ['large-tool', 'following'])
                self.assertEqual(len(blocks[0]['text']), len(output))
                self.assertTrue(blocks[0]['text'] == output, 'The large tool output must be byte-for-byte complete')
                self.assertEqual(blocks[1]['text'], 'after café ✓⚡')
                self.assertEqual(len(connections), 1)
                self.assertFalse(any(event['kind'] == 'disconnected' for event in self.events))
                self.backend.http.assert_not_awaited()
            finally:
                shutdown.set()
                await self.backend.close()

    async def test_snapshots_and_deltas_repair_reasoning(self):
        """An SSE reconnect snapshot must seed subsequent reasoning deltas correctly."""
        part = {'id': 'p', 'sessionID': 's', 'messageID': 'a', 'type': 'reasoning', 'text': 'first', 'time': {'start': 1000}}
        await self.backend.event({'type': 'message.part.updated', 'properties': {'sessionID': 's', 'part': part}})
        self.assertEqual(self.events[-1]['item']['parts'], {'0': 'first'})
        await self.backend.event({'type': 'message.part.delta', 'properties': {
            'sessionID': 's', 'messageID': 'a', 'partID': 'p', 'field': 'text', 'delta': ' second'}})
        self.assertEqual(self.backend.parts['p']['text'], 'first second')
        self.assertEqual(self.events[-1]['type'], 'reasoning')
        self.backend.messages['user'] = {'role': 'user'}
        await self.backend.event({'type': 'message.part.updated', 'properties': {
            'sessionID': 's', 'part': dict(part, id='userpart', messageID='user')}})
        self.assertEqual(len(self.events), 2)

    async def test_native_process_proxy_policy(self):
        """Reuse OS proxy defaults, preserve explicit choices, and always bypass loopback."""
        cases = [
            ({'NO_PROXY': 'internal.example'}, {'HTTP_PROXY': 'http://127.0.0.1:8123', 'HTTPS_PROXY': 'http://127.0.0.1:8123'}, 'internal.example', True),
            ({'HTTPS_PROXY': 'http://explicit.invalid:8123', 'no_proxy': '.internal'}, {'HTTPS_PROXY': 'http://explicit.invalid:8123'}, '.internal', False),
            ({'HTTPS_PROXY': ''}, {'HTTPS_PROXY': ''}, None, False),
            ({'ALL_PROXY': 'socks5://explicit.invalid:8123', 'NO_PROXY': '*'}, {'ALL_PROXY': 'socks5://explicit.invalid:8123'}, '*', False),
        ]
        for environment, expected, exclusion, inherited in cases:
            with self.subTest(environment=environment):
                process = SimpleNamespace(returncode=None,
                    stdout=SimpleNamespace(readline=AsyncMock(return_value=b'http://127.0.0.1:8124\n')),
                    terminate=lambda: None, wait=AsyncMock(return_value=0))
                client = SimpleNamespace(closed=False, close=AsyncMock())
                self.backend.http.return_value = {}
                self.backend.read = AsyncMock(side_effect=self.backend.ready.set)
                with (patch.dict(os.environ, environment, clear=True),
                      patch('flowcanvas.backends.opencode.shutil.which', return_value='/test/opencode'),
                      patch('flowcanvas.backends.opencode.getproxies', return_value={
                          'http': 'http://127.0.0.1:8123', 'https': 'http://127.0.0.1:8123'}) as system,
                      patch('flowcanvas.backends.opencode.asyncio.create_subprocess_exec', return_value=process) as spawn,
                      patch('flowcanvas.backends.opencode.aiohttp.ClientSession', return_value=client)):
                    await self.backend.connect()
                    actual = spawn.call_args.kwargs['env']
                    self.assertEqual({key: value for key, value in actual.items()
                                      if key in {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'}}, expected)
                    bypass = actual['NO_PROXY'].split(',')
                    self.assertTrue({'localhost', '127.0.0.1', '::1'} <= set(bypass))
                    self.assertEqual(actual['NO_PROXY'], actual['no_proxy'])
                    if exclusion: self.assertIn(exclusion, bypass)
                    self.assertEqual(system.called, inherited)
                    self.assertEqual(dict(os.environ), environment)
                    await self.backend.close()
                    self.backend.process = None

    async def test_reconnect_does_not_duplicate_buffered_text(self):
        """A native snapshot can already include deltas buffered in the newly opened SSE."""
        status = 'busy'
        part = {'id': 'p', 'sessionID': 's', 'messageID': 'a', 'type': 'text', 'text': 'Hello'}
        records = [{'info': {'id': 'u', 'role': 'user'}, 'parts': []},
                   {'info': {'id': 'a', 'role': 'assistant', 'parentID': 'u',
                             'finish': 'stop', 'time': {'completed': 1}}, 'parts': [part]}]

        async def http(method, path, body=None):
            """Expose an authoritative snapshot newer than the first queued SSE delta."""
            if path.endswith('/status'):
                return {'s': {'type': status}}
            if path.endswith('/message/a'):
                return copy.deepcopy(records[1])
            return copy.deepcopy(records)

        async def frames():
            """Replay an already-included suffix, then publish the authoritative idle state."""
            nonlocal status
            delta = {'type': 'message.part.delta', 'properties': {'sessionID': 's', 'messageID': 'a',
                     'partID': 'p', 'field': 'text', 'delta': 'lo'}}
            yield ('data: ' + json.dumps(delta) + '\n').encode(); yield b'\n'
            status = 'idle'
            yield b'data: {"type":"session.idle","properties":{"sessionID":"s"}}\n'; yield b'\n'

        class Response:
            """Minimal SSE response for exercising the real adapter read loop."""
            content = SimpleNamespace(iter_any=frames)

            async def __aenter__(self):
                """Yield the fake native stream."""
                return self

            async def __aexit__(self, *args):
                """Leave no native resources behind."""
                return False

            def raise_for_status(self):
                """Represent a successful native HTTP handshake."""
                return None

        self.backend.http = http
        self.backend.url = 'http://native-test'
        self.backend.client = SimpleNamespace(get=lambda *args, **kwargs: Response())
        self.backend.process = SimpleNamespace(returncode=0)
        await self.backend.read()
        text = ''
        visible = []
        for event in self.events:
            if event['kind'] == 'item' and event['item']['kind'] == 'text':
                text = event['item']['text']; visible.append(text)
            elif event['kind'] == 'delta' and event['type'] == 'text':
                text += event['text']; visible.append(text)
        self.assertTrue(visible)
        self.assertEqual(set(visible), {'Hello'})
        self.assertEqual([e for e in self.events if e['kind'] == 'turn'][-1]['status'], 'completed')

    async def test_late_idle_does_not_end_busy_turn(self):
        """An older idle event must not terminate an accepted newer prompt."""
        self.backend.reconcile = AsyncMock(return_value=True)
        self.backend.http.return_value = {'s': {'type': 'busy'}}
        await self.backend.settle('s')
        self.assertIn('s', self.backend.active)
        self.assertEqual(self.events, [])
        self.backend.http.return_value = {}
        await self.backend.settle('s')
        await self.backend.settle('s')
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]['status'], 'completed')

    async def test_idle_waits_for_the_latest_guidance_answer(self):
        """Old native completion cannot finish a turn whose new input is still in transit."""
        records = [{'info': {'id': 'u', 'role': 'user'}, 'parts': []},
                   {'info': {'id': 'a', 'role': 'assistant', 'parentID': 'u',
                             'finish': 'stop', 'time': {'completed': 1}}, 'parts': []}]
        entered, release = asyncio.Event(), asyncio.Event()
        submitted = []
        self.backend.selections['s'] = {'model': {'providerID': 'provider', 'modelID': 'model'}}

        async def http(method, path, body=None):
            """Delay guidance dispatch, then expose its saved but not yet answered input."""
            if method == 'POST':
                submitted.append(body['messageID']); entered.set(); await release.wait()
                records.append({'info': {'id': body['messageID'], 'role': 'user'}, 'parts': []})
                return
            return {} if path.endswith('/status') else copy.deepcopy(records)

        self.backend.http.side_effect = http
        steering = asyncio.create_task(self.backend.steer('s', 'u', 'additional request'))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await self.backend.settle('s')
            self.assertEqual(self.backend.active.get('s'), 'u')
            release.set(); await steering
            await self.backend.settle('s')
            self.assertEqual(self.backend.active.get('s'), 'u')
            records.append({'info': {'id': 'guided-answer', 'role': 'assistant', 'parentID': submitted[0],
                                     'finish': 'stop', 'time': {'completed': 2}}, 'parts': []})
            await self.backend.settle('s')
            self.assertNotIn('s', self.backend.active)
            self.assertEqual([e['status'] for e in self.events if e['kind'] == 'turn'], ['completed'])
        finally:
            release.set(); await asyncio.gather(steering, return_exceptions=True)

    async def test_idle_requires_a_complete_answer_to_the_last_user(self):
        """Persisted input, a tool round or an internal summary alone is not final completion."""
        for answer in (None, {'finish': 'tool-calls', 'time': {'completed': 1}},
                       {'finish': 'stop', 'summary': True, 'time': {'completed': 1}}, {'finish': 'stop'},
                       {'finish': 'stop', 'parentID': 'different-user', 'time': {'completed': 1}}):
            with self.subTest(answer=answer):
                self.backend.active['s'] = 'u'; self.events.clear()
                records = [{'info': {'id': 'u', 'role': 'user'}, 'parts': []}]
                if answer:
                    records.append({'info': {'id': 'a', 'role': 'assistant', 'parentID': 'u', **answer}, 'parts': []})
                self.backend.http.side_effect = lambda method, path, body=None: {} if path.endswith('/status') else records
                await self.backend.settle('s')
                self.assertEqual(self.backend.active.get('s'), 'u')
                self.assertFalse(any(e['kind'] == 'turn' for e in self.events))

    async def test_stop_cannot_be_overtaken_by_pending_guidance(self):
        """A submitted prompt cannot arrive after the stop operation has canceled the session."""
        entered, release = asyncio.Event(), asyncio.Event()
        native_busy = False
        self.backend.reconcile = AsyncMock(return_value=False)
        self.backend.selections['s'] = {}

        async def http(method, path, body=None):
            """Hold a prompt before native acceptance while cancellation runs concurrently."""
            nonlocal native_busy
            if path.endswith('/prompt_async'):
                entered.set(); await release.wait()
                native_busy = True
            elif path.endswith('/abort'):
                native_busy = False
            elif path.endswith('/status'):
                return {'s': {'type': 'busy'}} if native_busy else {}

        self.backend.http.side_effect = http
        steering = asyncio.create_task(self.backend.steer('s', 'u', 'late request'))
        stopping = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            stopping = asyncio.create_task(self.backend.interrupt('s', 'u'))
            await asyncio.sleep(.02)
            await self.backend.settle('s')
            self.assertEqual(self.backend.active.get('s'), 'u')
            expected = self.backend.inputs['s']
            with self.assertRaisesRegex(RuntimeError, 'no longer accepting'):
                await self.backend.steer('s', 'u', 'request after stop')
            self.assertEqual(self.backend.inputs['s'], expected)
            release.set()
            await asyncio.wait_for(asyncio.gather(steering, stopping, return_exceptions=True), 2)
            self.assertFalse(native_busy, 'Late guidance restarted native work after stop')
            self.assertNotIn('s', self.backend.active)
            self.assertEqual([e['status'] for e in self.events if e['kind'] == 'turn'], ['interrupted'])
        finally:
            release.set()
            await asyncio.gather(steering, *([stopping] if stopping else []), return_exceptions=True)

    async def test_new_guidance_invalidates_an_inflight_idle_check(self):
        """The last-input check must still match after awaiting native status."""
        entered, release = asyncio.Event(), asyncio.Event()
        self.backend.reconcile = AsyncMock(return_value=True)

        async def http(*args):
            """Hold an idle status response while a newer input acquires the task."""
            entered.set(); await release.wait()
            return {}

        self.backend.http.side_effect = http
        checking = asyncio.create_task(self.backend.settle('s'))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.backend.inputs['s'] = 'new-guidance'
            release.set(); await checking
            self.assertEqual(self.backend.active.get('s'), 'u')
            self.assertEqual(self.events, [])
        finally:
            release.set(); await asyncio.gather(checking, return_exceptions=True)

    async def test_native_failure_drains_pending_guidance_before_settling(self):
        """All native error paths cancel late accepted work before releasing its turn owner."""
        for origin in ('session', 'message', 'snapshot'):
            with self.subTest(origin=origin):
                events = []
                backend = OpenCodeBackend(Path('/tmp'), events.append)
                backend.active['s'] = 'u'; backend.selections['s'] = {}
                backend.message_turns.update(u='u', a='u')
                entered, release = asyncio.Event(), asyncio.Event()
                busy = False
                info = {'id': 'a', 'role': 'assistant', 'parentID': 'u',
                        'error': {'name': 'APIError', 'data': {'message': 'provider rejected request'}}}
                records = [{'info': {'id': 'u', 'role': 'user'}, 'parts': []}, {'info': info, 'parts': []}]

                async def http(method, path, body=None):
                    """Let the failed native session accept a delayed prompt unless it is canceled."""
                    nonlocal busy
                    if path.endswith('/prompt_async'):
                        entered.set(); await release.wait(); busy = True
                    elif path.endswith('/abort'):
                        busy = False
                    else:
                        return records

                backend.http = AsyncMock(side_effect=http)
                steering = asyncio.create_task(backend.steer('s', 'u', 'pending requirement'))
                failing = None
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    if origin == 'snapshot':
                        failing = asyncio.create_task(backend.reconcile('s'))
                    else:
                        event = {'type': 'session.error', 'properties': {'sessionID': 's', 'error': info['error']}}
                        if origin == 'message':
                            event = {'type': 'message.updated', 'properties': {'sessionID': 's', 'info': info}}
                        failing = asyncio.create_task(backend.event(event))
                    await asyncio.sleep(.02)
                    release.set()
                    await asyncio.wait_for(asyncio.gather(steering, failing), 2)
                    self.assertFalse(busy, 'Native failure left the delayed prompt executing without ownership')
                    self.assertNotIn('s', backend.active)
                    terminal = [e for e in events if e['kind'] == 'turn']
                    self.assertEqual([e['status'] for e in terminal], ['failed'])
                    self.assertEqual(terminal[0]['error'], 'provider rejected request')
                finally:
                    release.set()
                    await asyncio.gather(steering, *([failing] if failing else []), return_exceptions=True)
                    await backend.close()

    async def test_previous_turn_messages_cannot_mutate_current_turn(self):
        """Late parts, usage and assistant errors retain their original native parent."""
        old_info = {'id': 'old-answer', 'role': 'assistant', 'parentID': 'old-user'}
        old_part = {'id': 'old-part', 'sessionID': 's', 'messageID': 'old-answer',
                    'type': 'text', 'text': 'old reply'}
        old_record = {'info': old_info, 'parts': [old_part]}
        records = [{'info': {'id': 'old-user', 'role': 'user'}, 'parts': []}, old_record,
                   {'info': {'id': 'u', 'role': 'user'}, 'parts': []},
                   {'info': {'id': 'a', 'role': 'assistant', 'parentID': 'u', 'tokens': {'input': 10}}, 'parts': []}]
        events = [
            {'type': 'message.updated', 'properties': {'sessionID': 's', 'info': {
                **old_info, 'tokens': {'input': 999}, 'error': {'name': 'APIError', 'data': {'message': 'old failure'}}}}},
            {'type': 'message.updated', 'properties': {'sessionID': 's', 'info': {
                **old_info, 'error': {'name': 'MessageAbortedError'}}}},
            {'type': 'message.part.updated', 'properties': {'sessionID': 's', 'part': old_part}},
            {'type': 'message.part.delta', 'properties': {'sessionID': 's', 'messageID': 'old-answer',
                'partID': 'old-part', 'field': 'text', 'delta': ' late suffix'}},
        ]
        for recovering in (False, True):
            for event in events:
                with self.subTest(event=event['type'], recovering=recovering):
                    received = []
                    backend = OpenCodeBackend(Path('/tmp'), received.append)
                    backend.active['s'] = 'u'
                    backend.messages['old-answer'] = copy.deepcopy(old_info)
                    backend.messages['a'] = copy.deepcopy(records[-1]['info'])
                    backend.parts['old-part'] = copy.deepcopy(old_part)

                    async def http(method, path, body=None):
                        """Return ordered native history or the selected old message snapshot."""
                        return copy.deepcopy(old_record if path.endswith('/message/old-answer') else records)

                    backend.http = AsyncMock(side_effect=http)
                    if recovering:
                        backend.recovering.add('s')
                    try:
                        await backend.event(copy.deepcopy(event))
                        if backend.tasks:
                            await asyncio.wait_for(asyncio.gather(*backend.tasks), 2)
                        self.assertEqual(backend.active.get('s'), 'u')
                        self.assertNotIn('s', backend.stopping)
                        self.assertEqual(received, [])
                        await backend.event({'type': 'message.updated', 'properties': {
                            'sessionID': 's', 'info': records[-1]['info']}})
                        self.assertEqual(received[-1]['kind'], 'usage')
                        self.assertEqual(received[-1]['turn'], 'u')
                        self.assertEqual(received[-1]['usage']['last']['inputTokens'], 10)
                    finally:
                        await backend.close()

    async def test_unsupported_event_cancels_native_work_before_disconnect(self):
        """A fatal protocol shape must retire owned work, including cancellation uncertainty."""
        malformed = [
            {'type': 'message.updated', 'properties': {'sessionID': 's', 'info': {}}},
            {'type': 'message.updated', 'properties': {'sessionID': 's', 'info': []}},
            {'type': 'session.status', 'properties': None}, []]
        for payload, cancellation_fails in [(payload, fails) for payload in malformed for fails in (False, True)]:
            with self.subTest(payload=payload, cancellation_fails=cancellation_fails):
                events = []
                backend = OpenCodeBackend(Path('/tmp'), events.append)
                backend.active['s'] = 'u'
                backend.url = 'http://native-test'
                backend.reconcile = AsyncMock(return_value=False)
                process = SimpleNamespace(returncode=None, wait=AsyncMock(return_value=0))
                process.terminate = lambda: setattr(process, 'returncode', 0)
                process.kill = process.terminate
                aborted = asyncio.Event()

                async def http(method, path, body=None):
                    """Keep the fixture busy until the fatal reader requests native cancellation."""
                    if path == '/session/status':
                        return {'s': {'type': 'busy'}}
                    self.assertEqual(path, '/session/s/abort')
                    aborted.set()
                    if cancellation_fails:
                        raise RuntimeError('native cancellation endpoint unavailable')

                async def frames():
                    """Supply valid SSE framing with a missing field or incompatible object shape."""
                    yield ('data: ' + json.dumps(payload) + '\n\n').encode()

                class Response:
                    """Expose malformed SSE through the actual byte-framing and read loop."""
                    content = SimpleNamespace(iter_any=frames)

                    async def __aenter__(self):
                        """Enter a successful native stream."""
                        return self

                    async def __aexit__(self, *args):
                        """Keep parser errors visible to the owning reader."""
                        return False

                    def raise_for_status(self):
                        """Represent a successful HTTP handshake."""
                        return None

                client = SimpleNamespace(closed=False, get=lambda *args, **kwargs: Response())
                client.close = AsyncMock(side_effect=lambda: setattr(client, 'closed', True))
                backend.process, backend.client, backend.http = process, client, AsyncMock(side_effect=http)
                try:
                    async with backend.delivery_lock:
                        reader = backend.background(backend.read())
                        async with asyncio.timeout(2):
                            while not backend.closing:
                                await asyncio.sleep(0)
                        self.assertFalse(aborted.is_set(), 'Cancellation must follow pending delivery')
                        self.assertFalse(backend.ready.is_set())
                    await asyncio.wait_for(reader, 2)
                    self.assertTrue(aborted.is_set())
                    self.assertTrue(client.closed)
                    self.assertEqual(process.returncode, 0)
                    self.assertEqual(backend.active, {})
                    self.assertFalse(backend.ready.is_set())
                    self.assertEqual(len(events), 1)
                    self.assertEqual(events[0]['kind'], 'disconnected')
                    self.assertIn('unsupported event', events[0]['error'])
                    self.assertEqual('cancellation could not be confirmed' in events[0]['error'], cancellation_fails)
                finally:
                    try:
                        await backend.close()
                    except RuntimeError:
                        pass

    async def test_native_failure_cleanup_preserves_error_and_reaps_its_reader(self):
        """A failed abort can retire the calling event reader without self-await or false success."""
        for cancellation_fails in (False, True):
            with self.subTest(cancellation_fails=cancellation_fails):
                events, calls = [], []
                backend = OpenCodeBackend(Path('/tmp'), events.append)
                backend.active['s'] = 'u'
                process = SimpleNamespace(returncode=None, wait=AsyncMock(return_value=0))
                process.terminate = lambda: setattr(process, 'returncode', 0)
                process.kill = process.terminate
                client = SimpleNamespace(closed=False)
                client.close = AsyncMock(side_effect=lambda: setattr(client, 'closed', True))
                backend.process, backend.client = process, client

                async def http(method, path, body=None):
                    """Fail the first abort and optionally reject the shutdown retry too."""
                    self.assertTrue(path.endswith('/abort'))
                    calls.append(path)
                    if len(calls) == 1 or cancellation_fails:
                        raise RuntimeError('native cancellation endpoint unavailable')

                backend.http = AsyncMock(side_effect=http)
                reader = backend.background(backend.event({'type': 'session.error', 'properties': {
                    'sessionID': 's', 'error': {'name': 'APIError', 'data': {'message': 'original failure'}}}}))
                await asyncio.wait_for(reader, 2)
                self.assertTrue(client.closed)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(backend.active, {})
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['status'], 'failed')
                self.assertTrue(events[0]['error'].startswith('original failure'))
                self.assertEqual('cancellation could not be confirmed' in events[0]['error'], cancellation_fails)

    async def test_native_continuation_users_remain_in_current_turn(self):
        """Steering and compaction users join the active turn without replaying future text."""
        continuation = {'id': 'native-continuation', 'role': 'user'}
        answer = {'id': 'continued-answer', 'role': 'assistant', 'parentID': continuation['id']}
        part = {'id': 'continued-text', 'sessionID': 's', 'messageID': answer['id'],
                'type': 'text', 'text': 'first second'}
        self.backend.http.return_value = [
            {'info': {'id': 'u', 'role': 'user'}, 'parts': []},
            {'info': continuation, 'parts': []}, {'info': answer, 'parts': [part]}]
        await self.backend.event({'type': 'message.updated', 'properties': {'sessionID': 's', 'info': continuation}})
        self.assertEqual(self.events, [], 'Membership lookup must not project text ahead of queued SSE data')
        await self.backend.event({'type': 'message.updated', 'properties': {'sessionID': 's', 'info': answer}})
        await self.backend.event({'type': 'message.part.updated', 'properties': {'sessionID': 's', 'part': {**part, 'text': 'first'}}})
        await self.backend.event({'type': 'message.part.delta', 'properties': {
            'sessionID': 's', 'messageID': answer['id'], 'partID': part['id'], 'field': 'text', 'delta': ' second'}})
        text = ''
        for event in self.events:
            self.assertEqual(event['turn'], 'u')
            if event['kind'] == 'item': text = event['item']['text']
            elif event['kind'] == 'delta': text += event['text']
        self.assertEqual(text, 'first second')
        self.backend.http.assert_awaited_once()

    async def test_message_membership_lookup_cannot_cross_turn_boundary(self):
        """A delayed history lookup cannot attach its response to a newly active turn."""
        entered, release = asyncio.Event(), asyncio.Event()

        async def http(method, path, body=None):
            """Hold the native ownership lookup while the previous turn is replaced."""
            entered.set()
            await release.wait()
            return [{'info': {'id': 'u', 'role': 'user'}, 'parts': []},
                    {'info': {'id': 'unknown-answer', 'role': 'assistant', 'parentID': 'continuation'}, 'parts': []}]

        self.backend.http = AsyncMock(side_effect=http)
        pending = asyncio.create_task(self.backend.event({'type': 'message.updated', 'properties': {
            'sessionID': 's', 'info': {'id': 'unknown-answer', 'role': 'assistant', 'parentID': 'continuation'}}}))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.backend.active['s'] = 'next-user'
            release.set()
            await asyncio.wait_for(pending, 2)
            self.assertEqual(self.events, [])
            self.assertEqual(self.backend.active['s'], 'next-user')
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_context_overflow_signal_waits_for_native_recovery_or_terminal_error(self):
        """Native overflow can initiate compaction; persisted assistant errors still fail."""
        error = {'name': 'ContextOverflowError', 'data': {'message': 'context length exceeded'}}
        await self.backend.event({'type': 'session.error', 'properties': {'sessionID': 's', 'error': error}})
        self.assertEqual(self.backend.active.get('s'), 'u')
        self.assertEqual(self.events, [])
        self.backend.http.assert_not_awaited()
        await self.backend.event({'type': 'message.updated', 'properties': {'sessionID': 's', 'info': {
            'id': 'a', 'role': 'assistant', 'parentID': 'u', 'error': error}}})
        self.assertEqual(self.events[-1]['status'], 'failed')
        self.assertEqual(self.events[-1]['error'], error['data']['message'])

    async def test_usage_normalization_and_snapshot_recovery(self):
        """Preserve inclusive totals and cache detail without inventing task-wide accounting."""
        for origin in ('event', 'snapshot'):
            for reported_total in (None, 0, 180):
                with self.subTest(origin=origin, reported_total=reported_total):
                    self.events.clear()
                    tokens = {'input': 60, 'output': 18, 'reasoning': 12, 'cache': {'read': 40, 'write': 20}}
                    if reported_total is not None: tokens['total'] = reported_total
                    info = {'id': 'a', 'role': 'assistant', 'parentID': 'u', 'summary': True,
                            'tokens': tokens, 'cost': .015, 'time': {'completed': 2000}}
                    if origin == 'event':
                        await self.backend.event({'type': 'message.updated', 'properties': {'sessionID': 's', 'info': info}})
                    else:
                        self.backend.http.return_value = [
                            {'info': {'id': 'u', 'role': 'user'}, 'parts': []}, {'info': info, 'parts': []}]
                        await self.backend.reconcile('s')
                    self.assertEqual(self.events, [{'kind': 'usage', 'thread': 's', 'turn': 'u', 'usage': {
                        'last': {'inputTokens': 120, 'outputTokens': 30, 'cachedInputTokens': 40,
                                 'cacheWriteInputTokens': 20, 'reasoningOutputTokens': 12,
                                 'totalTokens': 150 if reported_total is None else reported_total}, 'cost': .015}}])
        self.events.clear()
        self.backend.project_record('s', 'u', {'info': {'id': 'a', 'role': 'assistant'}, 'parts': []})
        self.assertFalse(self.events, 'Missing usage is not a reported zero')

    async def test_compaction_summary_is_internal_but_usage_and_errors_are_visible(self):
        """Native context maintenance must not appear as an assistant reply or reasoning."""
        info = {'id': 'summary', 'role': 'assistant', 'parentID': 'u', 'summary': True,
                'tokens': {'input': 4000}, 'time': {'completed': 2000}}
        parts = [{'id': kind, 'sessionID': 's', 'messageID': 'summary', 'type': kind,
                  'text': 'internal context'} for kind in ('text', 'reasoning')]
        records = [{'info': {'id': 'u', 'role': 'user'}, 'parts': []}, {'info': info, 'parts': parts}]
        self.backend.http.return_value = records
        for recovering in (False, True):
            with self.subTest(recovering=recovering):
                self.events.clear()
                if recovering: self.backend.recovering.add('s')
                await self.backend.event({'type': 'message.updated', 'properties': {'sessionID': 's', 'info': info}})
                for part in parts:
                    await self.backend.event({'type': 'message.part.updated', 'properties': {'sessionID': 's', 'part': copy.deepcopy(part)}})
                    await self.backend.event({'type': 'message.part.delta', 'properties': {
                        'sessionID': 's', 'messageID': 'summary', 'partID': part['id'], 'field': 'text', 'delta': ' more'}})
                if self.backend.tasks:
                    # Snapshot refresh reads one message instead of the whole history.
                    self.backend.http.return_value = {'info': info, 'parts': parts}
                    await asyncio.wait_for(asyncio.gather(*self.backend.tasks), 2)
                    self.backend.http.return_value = records
                await self.backend.reconcile('s')
                self.assertTrue(any(e['kind'] == 'usage' for e in self.events))
                self.assertFalse(any(e['kind'] in {'item', 'delta'} for e in self.events))
                self.assertEqual(self.backend.active['s'], 'u')
        await self.backend.event({'type': 'message.updated', 'properties': {'sessionID': 's', 'info': {
            **info, 'error': {'name': 'APIError', 'data': {'message': 'summary unavailable'}}}}})
        self.assertEqual(self.events[-1]['status'], 'failed')
        self.assertEqual(self.events[-1]['error'], 'summary unavailable')

    async def test_child_history_omits_compaction_summaries(self):
        """Child conversation previews show real messages instead of internal context summaries."""
        self.backend.connect = AsyncMock()

        async def http(method, path, body=None):
            """Return one child with an internal summary followed by its actual response."""
            if path.endswith('/children'):
                return [{'id': 'child', 'title': 'Child'}] if path == '/session/s/children' else []
            if path == '/session/status': return {}
            return [
                {'info': {'id': 'user', 'role': 'user'}, 'parts': [{'type': 'text', 'text': 'Request'}]},
                {'info': {'id': 'compact', 'role': 'user'}, 'parts': [{'type': 'compaction', 'auto': True}]},
                {'info': {'id': 'summary', 'role': 'assistant', 'summary': True, 'time': {'completed': 1}},
                 'parts': [{'id': 'internal', 'type': 'text', 'text': 'Internal context'}]},
                {'info': {'id': 'continue', 'role': 'user'},
                 'parts': [{'type': 'text', 'synthetic': True, 'text': 'Continue automatically'}]},
                {'info': {'id': 'answer', 'role': 'assistant', 'time': {'completed': 2}},
                 'parts': [{'id': 'answer-text', 'type': 'text', 'text': 'Actual answer'}]},
            ]

        self.backend.http = AsyncMock(side_effect=http)
        result = await self.backend.agents('s')
        self.assertEqual([row['text'] for row in result['items']], ['Request', 'Actual answer'])
        self.assertEqual(result['agents'][0]['status'], 'completed')

    async def test_errors_and_abort_are_distinct(self):
        """Native aborts settle as interrupted; provider errors remain failures."""
        await self.backend.event({'type': 'session.error', 'properties': {'sessionID': 's', 'error': {'name': 'MessageAbortedError'}}})
        self.assertIn('s', self.backend.stopping)
        self.backend.reconcile = AsyncMock(return_value=True)
        self.backend.http.return_value = {}
        await self.backend.settle('s')
        self.assertEqual(self.events[-1]['status'], 'interrupted')
        self.backend.active['s'] = 'u2'
        await self.backend.event({'type': 'session.error', 'properties': {'sessionID': 's', 'error': {
            'name': 'APIError', 'data': {'message': 'provider unavailable'}}}})
        self.assertEqual(self.events[-1]['status'], 'failed')
        self.assertEqual(self.events[-1]['error'], 'provider unavailable')

    async def test_tool_progress_and_policy(self):
        """Only full access permits native writes; tools retain input, output and timing."""
        block = item({'id': 'p', 'type': 'tool', 'tool': 'bash', 'state': {
            'status': 'running', 'input': {'command': 'sleep 1'}, 'metadata': {'output': 'partial'}, 'time': {'start': 2000}}})
        self.assertEqual(block['text'], 'partial')
        self.assertEqual(block['input'], 'sleep 1')
        self.assertEqual(block['started_at'], 2)
        self.assertEqual(permissions('readonly')[0]['action'], 'deny')
        self.assertFalse(any(r['permission'] in {'bash', 'edit', 'write', 'task'} and r['action'] == 'allow' for r in permissions('readonly')))
        self.assertEqual(permissions('full')[0]['action'], 'allow')
        with self.assertRaises(ValueError): permissions('invalid')

    async def test_model_refresh_cannot_dispose_active_execution(self):
        """A directory-cache refresh is forbidden on an adapter owning a live turn."""
        self.backend.connect = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, 'active execution'):
            await self.backend.models(refresh=True)
        self.backend.http.assert_not_awaited()

    async def test_prompt_and_guidance_preserve_native_model_selection(self):
        """Prompts and guidance retain model choice, workflow constraints and source-link formatting."""
        self.backend.active.clear()
        self.backend.workflow_instructions = 'Use only the authorized workflow tools.'
        turn = await self.backend.send('s', 'hello', 'provider/model', 'low')
        prompt = self.backend.http.call_args.args[2]
        self.assertEqual(prompt['model'], {'providerID': 'provider', 'modelID': 'model'})
        self.assertEqual(prompt['variant'], 'low')
        self.assertIn(self.backend.workflow_instructions, prompt['system'])
        self.assertIn('[file.ext:12](/absolute/path/file.ext#L12)', prompt['system'])
        self.assertIn('verified line numbers', prompt['system'])
        await self.backend.steer('s', turn, 'new guidance')
        guidance = self.backend.http.call_args.args[2]
        self.assertEqual(guidance['model'], prompt['model'])
        self.assertEqual(guidance['variant'], 'low')
        self.assertEqual(guidance['system'], prompt['system'])
        self.backend.active.clear()
        self.backend.workflow_instructions = None
        await self.backend.send('s', 'use local defaults', 'provider/model', 'default')
        self.assertNotIn('variant', self.backend.http.call_args.args[2])
        self.assertIn('[file.ext:12](/absolute/path/file.ext#L12)', self.backend.http.call_args.args[2]['system'])

    async def test_cancelled_prompt_wait_retains_native_ownership(self):
        """Cancelling a local HTTP waiter is not evidence that native execution stopped."""
        self.backend.active.clear()
        entered = asyncio.Event()

        async def http(method, path, body=None):
            """Hold a prompt after dispatch while the caller begins shutting down."""
            if path.endswith('/prompt_async'):
                entered.set()
                await asyncio.Future()

        self.backend.http.side_effect = http
        sender = asyncio.create_task(self.backend.send('s', 'task', 'provider/model', 'low'))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            turn = self.backend.active['s']
            sender.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await sender
            self.assertEqual(self.backend.active.get('s'), turn)
            self.assertEqual([e['status'] for e in self.events if e['kind'] == 'turn'], ['inProgress'])
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    async def test_lost_ack_recovers_accepted_prompt_without_resend(self):
        """A recorded user ID or completed native turn takes precedence over a lost response."""
        for finished in (False, True):
            with self.subTest(already_finished=finished):
                self.backend.active.clear(); self.events.clear()
                posted, reads = [], []

                async def http(method, path, body=None):
                    """Accept exactly one prompt but lose its HTTP acknowledgment."""
                    if path.endswith('/prompt_async'):
                        posted.append(body['messageID'])
                        if finished: self.backend.finish('s', 'completed')
                        raise RuntimeError('response lost')
                    if method == 'GET':
                        reads.append(path)
                        if len(reads) == 1: raise RuntimeError('message is not persisted yet')
                        return {'info': {'id': posted[0], 'sessionID': 's', 'role': 'user'}, 'parts': []}

                self.backend.http.side_effect = http
                with (patch.object(self.backend, 'close', new_callable=AsyncMock) as close,
                      patch.object(self.backend, 'process', SimpleNamespace(returncode=None))):
                    turn = await self.backend.send('s', 'task', 'provider/model', 'low')
                    self.assertEqual(posted, [turn])
                    self.assertEqual(self.backend.active.get('s'), None if finished else turn)
                    self.assertEqual(len(reads), 0 if finished else 2)
                    close.assert_not_awaited()
                    self.assertEqual([e['status'] for e in self.events if e['kind'] == 'turn'],
                                     ['inProgress', 'completed'] if finished else ['inProgress'])

    async def test_lost_ack_without_receipt_closes_owned_native_work(self):
        """Unknown acceptance is never retried or forgotten before native cleanup is attempted."""
        for close_error in (False, True):
            with self.subTest(close_error=close_error):
                self.backend.active.clear(); self.events.clear()
                posted, owned = [], []

                async def http(method, path, body=None):
                    """Lose the send response and make the receipt query unavailable."""
                    if path.endswith('/prompt_async'):
                        posted.append(body['messageID'])
                        raise RuntimeError('response lost')
                    if method == 'GET': raise RuntimeError('receipt unavailable')

                async def close():
                    """Observe native cancellation ownership, including a failed confirmation."""
                    owned.append(dict(self.backend.active))
                    self.backend.active.clear()
                    if close_error: raise RuntimeError('abort unavailable')

                self.backend.http.side_effect = http
                with patch.object(self.backend, 'close', side_effect=close):
                    with self.assertRaisesRegex(RuntimeError, 'submission could not be confirmed'):
                        await self.backend.send('s', 'task', 'provider/model', 'low')
                self.assertEqual(len(posted), 1)
                self.assertEqual(owned, [{'s': posted[0]}])
                terminal = [e for e in self.events if e['kind'] == 'turn' and e['status'] != 'inProgress']
                self.assertEqual([e['status'] for e in terminal], ['failed'])
                self.assertEqual('cancellation could not be confirmed' in terminal[0]['error'], close_error)

    async def test_lost_guidance_ack_recovers_its_exact_message(self):
        """Guidance has a receipt distinct from the running turn and is never resent."""
        self.backend.selections['s'] = {'model': {'providerID': 'provider', 'modelID': 'model'}}
        posted = []

        async def http(method, path, body=None):
            """Confirm only the specifically submitted guidance message after response loss."""
            if method == 'POST':
                posted.append(body.get('messageID'))
                raise RuntimeError('response lost')
            self.assertEqual(path, '/session/s/message/' + posted[0])
            return {'info': {'id': posted[0], 'sessionID': 's', 'role': 'user'}, 'parts': []}

        self.backend.http.side_effect = http
        with patch.object(self.backend, 'close', new_callable=AsyncMock) as close:
            await self.backend.steer('s', 'u', 'change requirement')
            close.assert_not_awaited()
        self.assertEqual(len(posted), 1)
        self.assertNotEqual(posted[0], 'u')
        self.assertEqual(self.backend.active['s'], 'u')
        self.assertEqual(self.backend.message_turns[posted[0]], 'u')


if __name__ == '__main__':
    unittest.main()
