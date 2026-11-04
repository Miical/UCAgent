"""Opt-in Codex baseline for shared chat API changes, using only a temporary project."""
import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
import httpx

from flowcanvas.chat import ChatService, routes
from flowcanvas.subagents import execution_active, thread_items


@unittest.skipUnless(os.environ.get('FLOWCANVAS_TEST_CODEX') == '1', 'requires a configured local Codex account')
class CodexLiveTests(unittest.IsolatedAsyncioTestCase):
    """Exercise native generation and history without touching existing conversations."""

    async def asyncSetUp(self):
        """Suppress asyncio debug representations of native provider responses."""
        asyncio.get_running_loop().set_debug(False)
        logger = logging.getLogger('httpx')
        previous = logger.level
        logger.setLevel(logging.WARNING)
        self.addCleanup(logger.setLevel, previous)

    async def test_public_queue_reconnect_and_resume(self):
        """Retain tools, queued turns, task cards and policy across native and UI restarts."""
        with tempfile.TemporaryDirectory(prefix='flowcanvas-codex-api-') as directory:
            root = Path(directory)
            service = ChatService(root)
            service.flusher = asyncio.create_task(service.flush())
            probe = service.workspace / 'probe.txt'
            probe.write_text('CODEX_FILE_732\n')
            original_files = {p.name: p.read_bytes() for p in service.workspace.iterdir() if p.is_file()}
            app = FastAPI(); app.include_router(routes(service))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
            identity = native = None
            try:
                catalog = await service.backends['codex'].models()
                service.catalogs[('codex', str(service.workspace))] = catalog
                model = next((m for m in catalog if m['default']), catalog[0])
                effort = 'low' if 'low' in model['efforts'] else model['default_effort']
                selected = {'model': model['id'], 'effort': effort}
                owner = (await client.post('/api/chat/sessions', json={'backend': 'codex'})).json()
                identity = owner['id']; endpoint = '/api/chat/sessions/' + identity
                self.assertEqual((await client.post(endpoint + '/permissions', json={'mode': 'readonly'})).status_code, 200)

                async def completed(user_count):
                    """Observe this same session until its queued turns and child check settle."""
                    async with asyncio.timeout(150):
                        while True:
                            current = (await client.get(endpoint)).json()
                            if current['status'] in {'error', 'interrupted'}:
                                self.fail(current.get('error') or current['status'])
                            if (current['status'] == 'completed' and not current.get('queue')
                                    and not execution_active(current)
                                    and sum(i['kind'] == 'user' for i in current['items']) == user_count):
                                return current
                            await asyncio.sleep(.1)

                first = await client.post(endpoint + '/messages', json={**selected,
                    'text': 'Remember CODEX_MEMORY_732. Read probe.txt with a tool and reply with its exact contents. '
                            'Do not modify files, invoke workflows or delegate.',
                    'task_card': {'title': 'Codex integration check', 'workflow': '', 'project': 'temporary test'}})
                self.assertEqual(first.status_code, 202)
                second = await client.post(endpoint + '/messages', json={**selected,
                    'text': 'Reply with only the memory marker from my first message. Do not use tools or delegate.'})
                self.assertEqual(second.status_code, 202)
                self.assertEqual(len(second.json()['queue']), 1)
                current = await completed(2)
                native = current['thread']
                texts = '\n'.join(i['text'] for i in current['items'] if i['kind'] == 'text')
                self.assertIn('CODEX_FILE_732', texts)
                self.assertIn('CODEX_MEMORY_732', texts)
                self.assertTrue(any(i['kind'] == 'tool' and i['status'] == 'completed' for i in current['items']))
                self.assertEqual(current['items'][0]['task_run']['status'], 'completed')
                self.assertEqual(current['permission_mode'], 'readonly')
                backend = service.session_backends[identity]
                self.assertEqual(backend.loaded[native], 'readonly')
                old_process = backend.process
                before = [(i['id'], i['text']) for i in current['items']]
                response = await client.post(endpoint + '/reconnect')
                self.assertEqual(response.status_code, 200)
                self.assertIsNotNone(old_process.returncode)
                self.assertEqual([(i['id'], i['text']) for i in response.json()['items']], before)
                self.assertEqual(response.json()['thread'], native)
                print('Codex real tools, queue, task card and native reconnect: passed', flush=True)

                await client.aclose(); await service.close()
                service = ChatService(root)
                service.flusher = asyncio.create_task(service.flush())
                restored = service.get(identity)
                self.assertEqual(restored['thread'], native)
                self.assertEqual(restored['backend'], 'codex')
                self.assertEqual(restored['permission_mode'], 'readonly')
                self.assertEqual([(i['id'], i['text']) for i in restored['items']], before)
                self.assertTrue(restored['queue_paused'])
                app = FastAPI(); app.include_router(routes(service))
                client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
                third = await client.post(endpoint + '/messages', json={**selected,
                    'text': 'Without tools, repeat both the memory marker and the exact file contents from our earlier turns. '
                            'Do not modify files or delegate.'})
                self.assertEqual(third.status_code, 202)
                current = await completed(3)
                self.assertEqual(current['thread'], native)
                text = next(i['text'] for i in reversed(current['items']) if i['kind'] == 'text')
                self.assertIn('CODEX_MEMORY_732', text)
                self.assertIn('CODEX_FILE_732', text)
                backend = service.session_backends[identity]
                self.assertEqual(backend.loaded[native], 'readonly')
                entries = await thread_items(backend, native)
                self.assertEqual(sum(i['item']['type'] == 'userMessage' for i in entries), 3)
                turns = await backend.request('thread/turns/list', {
                    'threadId': native, 'limit': 10, 'sortDirection': 'asc', 'itemsView': 'summary'})
                self.assertEqual([t['status'] for t in turns['data']], ['completed'] * 3)
                self.assertEqual({p.name: p.read_bytes() for p in service.workspace.iterdir() if p.is_file()}, original_files)
                saved = json.loads((root / (identity + '.json')).read_text())
                self.assertEqual(saved['thread'], native)
                self.assertEqual(saved['items'][0]['task_run']['status'], 'completed')
                self.assertEqual(len({i['id'] for i in current['items']}), len(current['items']))
                print('Codex persisted history, unchanged project and same-thread continuation: passed', flush=True)
            finally:
                # Archive only the thread created by this test. Native history remains
                # archived; this does not delete or scan any existing user history.
                try:
                    if identity:
                        native = native or service.get(identity).get('thread')
                        current = service.get(identity)
                        if current['status'] in {'starting', 'running', 'stopping'}:
                            await client.post('/api/chat/sessions/' + identity + '/stop')
                    if native and identity:
                        backend = service.session_backends.get(identity) or service.backends['codex']
                        await backend.connect()
                        await backend.request('thread/archive', {'threadId': native})
                        print('owned Codex test thread archived', flush=True)
                finally:
                    await client.aclose(); await service.close()
