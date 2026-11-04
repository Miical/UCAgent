"""Real workflow-host lifecycle checks without a model or simulator."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from flowcanvas.chat import ChatService
from flowcanvas.workflow_runtime import WorkflowRuntime


class WorkflowHostTests(unittest.IsolatedAsyncioTestCase):
    """Verify acceptance and the final MCP response from a disposable native host."""

    async def test_local_host_does_not_use_environment_proxy(self):
        """Owned loopback MCP traffic must bypass proxies even without user exclusions."""
        asyncio.get_running_loop().set_debug(False)
        requests = []

        async def reject(reader, writer):
            """Expose an invalid route without sending any local tool data externally."""
            try:
                requests.append(await asyncio.wait_for(reader.readline(), 5))
                writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        proxy = await asyncio.start_server(reject, '127.0.0.1', 0)
        address = 'http://127.0.0.1:' + str(proxy.sockets[0].getsockname()[1])
        environment = {key: address for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                                                'http_proxy', 'https_proxy', 'all_proxy')}
        environment.update(NO_PROXY='', no_proxy='')
        async with proxy:
            with tempfile.TemporaryDirectory(prefix='flowcanvas-host-proxy-') as directory, patch.dict(os.environ, environment):
                root = Path(directory)
                rtl = root / 'Probe.v'
                rtl.write_text('module Probe(input a, output y); assign y = ~a; endmodule\n')
                service = ChatService(root / 'state')
                owner = service.create()
                owner.update(status='running', attachment={'workflow_id': 'rtl-summary'})
                runtime = WorkflowRuntime(service, owner)
                try:
                    started = await runtime.run('rtl-summary', str(rtl), 'Probe')
                    self.assertTrue(started['tools'])
                    self.assertEqual(requests, [])
                finally:
                    await runtime.close()
                    await service.close()

    async def test_exit_delivers_response_before_host_shutdown(self):
        """Reject early Exit, return accepted Exit, and allow an independent next run."""
        asyncio.get_running_loop().set_debug(False)
        with tempfile.TemporaryDirectory(prefix='flowcanvas-host-exit-') as directory:
            root = Path(directory)
            rtl = root / 'Probe.v'
            rtl.write_text('module Probe(input a, output y); assign y = ~a; endmodule\n')
            service = ChatService(root / 'state')
            owner = service.create()
            owner.update(status='running', attachment={'workflow_id': 'rtl-summary'})
            runtime = WorkflowRuntime(service, owner)
            try:
                started = await runtime.run('rtl-summary', str(rtl), 'Probe')
                card = started['card']; identity = card['id']
                early = await runtime.invoke(identity, 'Exit', {})
                self.assertFalse(early['result'].get('isError', False))
                self.assertIs(yaml.safe_load(early['result']['content'][0]['text'])['exit'], False)
                self.assertIsNone(runtime.processes[identity].returncode)
                self.assertFalse((Path(card['root']) / 'complete.json').exists())
                await runtime.invoke(identity, 'ReadTextFile', {'path': 'Probe/Probe.v'})
                await runtime.invoke(identity, 'EditTextFile', {
                    'path': 'summary/rtl_summary.md',
                    'content': '\n# Probe\n\nThe combinational output y is the inverse of input a.\n'})
                await runtime.invoke(identity, 'SetCurrentStageJournal', {
                    'journal': 'Read Probe/Probe.v and wrote summary/rtl_summary.md from the assign expression.'})
                await runtime.invoke(identity, 'Complete', {})
                async with asyncio.timeout(5):
                    while not (Path(card['root']) / 'complete.json').exists():
                        await asyncio.sleep(.1)
                result = await runtime.invoke(identity, 'Exit', {})
                self.assertFalse(result['result'].get('isError', False))
                self.assertIs(yaml.safe_load(result['result']['content'][0]['text'])['exit'], True)
                self.assertEqual(await asyncio.wait_for(runtime.processes[identity].wait(), 10), 0)
                receipt = json.loads((Path(card['root']) / 'complete.json').read_text())
                self.assertTrue(receipt['completed'])
                self.assertEqual(result['card']['status'], 'completed')
                await runtime.inspect()
                self.assertFalse(card['alive'])
                self.assertFalse(any(tool['busy'] for tool in card['tui']['tools']))
                with self.assertRaisesRegex(ValueError, 'MCP is stopped'):
                    await runtime.invoke(identity, 'Status', {})
                self.assertEqual(runtime.processes[identity].returncode, 0)

                # A final-session DELETE can race the accepted host shutdown.
                # Its cleanup must neither revive that run nor poison the next one.
                next_run = await runtime.run('rtl-summary', str(rtl), 'Probe')
                next_card = next_run['card']
                self.assertNotEqual(next_card['id'], identity)
                self.assertEqual(next_card['status'], 'running')
                self.assertTrue(next_run['tools'])
                read = await runtime.invoke(next_card['id'], 'ReadTextFile', {'path': 'Probe/Probe.v'})
                self.assertFalse(read['result'].get('isError', False))
                self.assertIn('assign y = ~a', str(read['result']['content']))
                self.assertEqual(card['status'], 'completed')
                self.assertFalse(card['alive'])
                self.assertFalse((Path(next_card['root']) / 'complete.json').exists())
                self.assertEqual(len(owner['workflow_runs']), 2)
            finally:
                await runtime.close()
                await service.close()


if __name__ == '__main__':
    unittest.main()
