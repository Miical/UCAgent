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
from flowcanvas.workflow_bridge import WorkflowBridge


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
                service.projects.state['current'] = {'path': str(root)}
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
            (root / '.flowcanvas').mkdir()
            metadata = {'schema_version': 1, 'id': 'fixture', 'name': 'Probe',
                        'rtl': {'top': 'Probe', 'top_file': 'Probe.v'}}
            marker = root / '.flowcanvas/project.json'
            marker.write_text(json.dumps(metadata))
            service.projects.state['current'] = {'path': str(root)}
            owner = service.create()
            owner.update(status='running', attachment={'workflow_id': 'rtl-summary'})
            runtime = WorkflowRuntime(service, owner)
            try:
                started = await runtime.run('rtl-summary')
                card = started['card']; identity = card['id']
                report = root / 'output/rtl-summary/Probe' / identity / 'summary.md'
                self.assertEqual(card['report_file'], str(report))
                self.assertEqual(card['source_file'], str(rtl))
                self.assertIn(str(report), started['instruction'])
                self.assertIn('一段', started['instruction'])
                self.assertNotIn('一句话', started['instruction'])
                self.assertFalse(report.exists())
                early = await runtime.invoke(identity, 'Exit', {})
                self.assertFalse(early['result'].get('isError', False))
                self.assertIs(yaml.safe_load(early['result']['content'][0]['text'])['exit'], False)
                self.assertIsNone(runtime.processes[identity].returncode)
                self.assertFalse((Path(card['root']) / 'complete.json').exists())
                await runtime.invoke(identity, 'ReadTextFile', {'path': 'Probe/Probe.v'})
                await runtime.invoke(identity, 'EditTextFile', {
                    'path': 'summary/summary.md',
                    'content': '\n# Probe\n\nThe combinational output y is the inverse of input a.\n'})
                await runtime.invoke(identity, 'SetCurrentStageJournal', {
                    'journal': 'Read Probe/Probe.v and wrote summary/summary.md from the assign expression.'})
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
                self.assertEqual(receipt['report_file'], str(report))
                self.assertIn('inverse of input a', report.read_text())
                self.assertEqual(json.loads(marker.read_text()), metadata)
                self.assertEqual(rtl.read_text(), 'module Probe(input a, output y); assign y = ~a; endmodule\n')
                self.assertEqual(result['card']['status'], 'completed')
                await runtime.inspect()
                self.assertFalse(card['alive'])
                self.assertFalse(any(tool['busy'] for tool in card['tui']['tools']))
                with self.assertRaisesRegex(ValueError, 'MCP is stopped'):
                    await runtime.invoke(identity, 'Status', {})
                self.assertEqual(runtime.processes[identity].returncode, 0)

                # A final-session DELETE can race the accepted host shutdown.
                # Its cleanup must neither revive that run nor poison the next one.
                other = root / 'Other.v'
                other.write_text('module Other(input a, output y); assign y = a; endmodule\n')
                next_run = await runtime.run('rtl-summary', 'Other.v', 'Other')
                next_card = next_run['card']
                self.assertNotEqual(next_card['id'], identity)
                self.assertNotEqual(next_card['report_file'], str(report))
                self.assertEqual(next_card['top_module'], 'Other')
                self.assertEqual(next_card['source_file'], str(other))
                self.assertFalse(Path(next_card['report_file']).exists())
                self.assertTrue(report.is_file())
                self.assertEqual(next_card['status'], 'running')
                self.assertTrue(next_run['tools'])
                read = await runtime.invoke(next_card['id'], 'ReadTextFile', {'path': 'Other/Other.v'})
                self.assertFalse(read['result'].get('isError', False))
                self.assertIn('assign y = a', str(read['result']['content']))
                self.assertEqual(card['status'], 'completed')
                self.assertFalse(card['alive'])
                self.assertFalse((Path(next_card['root']) / 'complete.json').exists())
                self.assertEqual(len(owner['workflow_runs']), 2)
            finally:
                await runtime.close()
                await service.close()


if __name__ == '__main__':
    unittest.main()


class WorkflowGatewayTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the fixed MCP gateway against a real UCAgent host."""

    async def test_fixed_catalog_and_guarded_dispatch(self):
        """Keep discovery stable, reject invalid calls, and preserve host denials."""
        asyncio.get_running_loop().set_debug(False)
        with tempfile.TemporaryDirectory(prefix='flowcanvas-gateway-') as directory:
            root = Path(directory)
            rtl = root / 'Probe.v'
            rtl.write_text('module Probe(input a, output y); assign y = a; endmodule')
            service = ChatService(root / 'state')
            service.projects.state['current'] = {'path': str(root)}
            owner = service.create()
            owner['status'] = 'running'
            bridge = WorkflowBridge(service, owner)
            try:
                names = {tool.name for tool in await bridge.mcp.list_tools()}
                self.assertEqual(names, {'list_workflow_runs', 'run_workflow',
                    'call_workflow_tool', 'verification_targets', 'test_cases',
                    'test_case_runs', 'bug_records'})
                started = await bridge.mcp.call_tool('run_workflow', {
                    'workflow_id': 'rtl-summary', 'rtl_file': str(rtl), 'dut': 'Probe'})
                self.assertFalse(started.is_error)
                data = json.loads(started.content[0].text)
                card_id = data['card']['id']
                self.assertEqual(names, {tool.name for tool in await bridge.mcp.list_tools()})
                self.assertNotIn('workflow_tool_catalog', owner)
                for args, message in [
                    ({'card_id': 'foreign', 'tool_name': 'ReadTextFile', 'arguments': {}}, 'belong'),
                    ({'card_id': card_id, 'tool_name': 'Unknown', 'arguments': {}}, 'not exposed'),
                    ({'card_id': card_id, 'tool_name': '', 'arguments': {}}, 'tool_name'),
                    ({'card_id': card_id, 'tool_name': 'ReadTextFile', 'arguments': {'path': 42}}, 'Invalid arguments'),
                    ({'card_id': card_id, 'tool_name': 'ReadTextFile', 'arguments': {}}, 'Invalid arguments'),
                ]:
                    result = await bridge.mcp.call_tool('call_workflow_tool', args)
                    self.assertTrue(result.is_error, str(result))
                    self.assertIn(message, str(result.content))
                result = await bridge.mcp.call_tool('call_workflow_tool', {
                    'card_id': card_id, 'tool_name': 'ReadTextFile',
                    'arguments': {'path': 'Probe/Probe.v'}})
                self.assertFalse(result.is_error)
                self.assertIn('assign y = a', str(result.content))
                early = await bridge.mcp.call_tool('call_workflow_tool', {
                    'card_id': card_id, 'tool_name': 'Exit', 'arguments': {}})
                self.assertIs(yaml.safe_load(early.content[0].text)['exit'], False)
                self.assertFalse((Path(data['card']['root']) / 'complete.json').exists())
                for name, arguments in [
                    ('EditTextFile', {'path': 'summary/summary.md', 'content': '# Probe\n\nOutput y follows input a.\n'}),
                    ('SetCurrentStageJournal', {'journal': 'Read RTL and documented the combinational assignment.'}),
                    ('Complete', {}),
                    ('Exit', {}),
                ]:
                    result = await bridge.mcp.call_tool('call_workflow_tool', {
                        'card_id': card_id, 'tool_name': name, 'arguments': arguments})
                    self.assertFalse(result.is_error, str(result))
                self.assertTrue(Path(data['card']['report_file']).is_file())
                self.assertEqual(names, {tool.name for tool in await bridge.mcp.list_tools()})

            finally:
                await bridge.close()
                await service.close()
