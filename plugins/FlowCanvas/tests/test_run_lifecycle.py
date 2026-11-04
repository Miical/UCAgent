"""Real local command and MCP checks for test completion and coverage post-processing."""
import asyncio
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from flowcanvas.chat import ChatService
from flowcanvas.coverage_runtime import state
from flowcanvas.test_runner import TestRuns
from flowcanvas.testcases import Cases, execute
from flowcanvas.workflow_bridge import WorkflowBridge


class RunLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Keep the test process outcome separate from unfinished or failed collection."""

    async def test_coverage_stages_remain_running_until_finalized(self):
        """Exercise real extraction, merge, failure and cancellation through the public MCP tool."""
        asyncio.get_running_loop().set_debug(False)
        for outcome in ('merged', 'extract_failed', 'merge_failed', 'interrupted_extract', 'interrupted_merge'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory(prefix='flowcanvas-run-lifecycle-') as directory:
                root = Path(directory); project = root / 'project'; project.mkdir()
                source = project / 'test_probe.py'
                source.write_text('"""Deterministic local test fixture, not hardware coverage."""\n'
                    'def test_probe():\n'
                    '    """Execute an ordinary assertion and emit the test completion marker."""\n'
                    '    assert 1 + 1 == 2\n'
                    '    print("FC_TEST_EXITED", flush=True)\n'
                    'if __name__ == "__main__":\n'
                    '    test_probe()\n')
                script = project / 'collection_fixture.py'
                script.write_text('"""Gated local coverage fixture for lifecycle checks only."""\n'
                    'import json\nfrom pathlib import Path\nimport sys\nimport time\n'
                    'phase, outcome, root, artifact, *remaining = sys.argv[1:]\n'
                    'root, artifact = Path(root), Path(artifact)\n'
                    'print("FC_" + phase.upper() + "_WAITING", file=sys.stderr, flush=True)\n'
                    '(root / (phase + "-entered")).write_text("ready")\n'
                    'deadline = time.monotonic() + 20\n'
                    'while not (root / (phase + "-release")).exists():\n'
                    '    if time.monotonic() > deadline: raise TimeoutError("fixture gate was not released")\n'
                    '    time.sleep(.02)\n'
                    'if outcome == phase + "_failed":\n'
                    '    print("FC_" + phase.upper() + "_FAILED", file=sys.stderr, flush=True)\n'
                    '    raise SystemExit(2)\n'
                    'if phase == "extract":\n'
                    '    (artifact / "coverage.json").write_text(json.dumps({"fixture": True}))\n'
                    'else:\n'
                    '    inputs = json.loads(Path(remaining[0]).read_text())\n'
                    '    assert len(inputs) == 1 and json.loads(Path(inputs[0]).read_text())["fixture"]\n'
                    '    report = {"schema_version": 1, "build_id": "fixture-build",\n'
                    '        "instances": [{"path": "fixture", "module": "fixture", "covered": 1, "total": 2}]}\n'
                    '    (artifact / "report.json").write_text(json.dumps(report))\n'
                    'print("FC_" + phase.upper() + "_DONE", file=sys.stderr, flush=True)\n')
                python = shlex.quote(sys.executable)
                execute(project, {'operation': 'configure', 'expected_revision': 0, 'values': {
                    'command': python + ' test_probe.py {case}', 'description': 'Execute the local assertion fixture.'}})
                store = Cases(project)
                candidate = store.discover(['test_probe.py'])['cases'][0]
                store.import_files(['test_probe.py'], store.read().revision, [{**candidate,
                    'reason': 'Directly executable Python assertion fixture.', 'title': 'Local lifecycle probe',
                    'description': 'Complete one assertion before controlled coverage post-processing.'}])
                execute(project, {'operation': 'configure_coverage', 'expected_revision': store.read().revision,
                    'values': {'coverage': {'tool': 'local-fixture', 'description': 'Synthetic integration data, not measured hardware coverage.',
                        'artifact_path': '{run_dir}/coverage.json',
                        'extract_command': python + ' collection_fixture.py extract ' + outcome + ' {project} {run_dir}',
                        'merge_command': python + ' collection_fixture.py merge ' + outcome + ' {project} {merge_dir} {inputs_manifest}',
                        'report_path': '{merge_dir}/report.json',
                        'build_id_command': python + ' -c ' + shlex.quote('print("fixture-build")')}}})
                service = ChatService(root / 'state'); service.test_runs = TestRuns()
                service.projects.activate({'id': 'fixture', 'path': str(project), 'name': 'Lifecycle fixture', 'needs_import': False})
                owner = service.create(); owner['permission_mode'] = 'full'
                bridge = WorkflowBridge(service, owner); service.workflow_bridges[owner['id']] = bridge
                try:
                    await bridge.start()
                    async with (httpx2.AsyncClient(trust_env=False, timeout=httpx2.Timeout(30)) as http,
                                streamable_http_client(bridge.url, http_client=http) as streams,
                                ClientSession(*streams[:2]) as client):
                        await client.initialize()

                        async def call(**arguments):
                            """Decode actual MCP responses without using internal runner objects as tool results."""
                            result = await client.call_tool('test_case_runs', arguments)
                            self.assertFalse(result.is_error, str(result.content))
                            return result.structured_content or json.loads(result.content[0].text)

                        started = await call(operation='start', case_id=store.read().targets[0].id)
                        run_id = started['run']['id']; task = service.test_runs.tasks[run_id]
                        during = []
                        for phase in ('extract', 'merge'):
                            async with asyncio.timeout(10):
                                while not (project / (phase + '-entered')).is_file():
                                    self.assertFalse(task.done(), 'Collection ended before its observation gate')
                                    await asyncio.sleep(.02)
                            output = await call(operation='read', run_id=run_id, offset=0, wait_seconds=0)
                            self.assertIn('FC_TEST_EXITED', output['text'])
                            self.assertIn('FC_' + phase.upper() + '_WAITING', output['text'])
                            self.assertEqual(output['run']['status'], 'running')
                            self.assertIsNone(output['run']['ended'])
                            self.assertFalse(task.done())
                            self.assertEqual(state(project)['runs'], [])
                            waiting = await call(operation='read', run_id=run_id, offset=output['offset'], wait_seconds=0)
                            self.assertEqual(waiting['text'], '')
                            self.assertEqual(waiting['run']['status'], 'running')
                            during.append({'phase': phase, 'tool_status': output['run']['status'],
                                'coverage_status': output['run']['coverage_status'], 'ended': output['run']['ended'],
                                'empty_tail_still_running': True})
                            if outcome == 'interrupted_' + phase:
                                process = service.test_runs.processes[run_id]
                                await call(operation='stop', run_id=run_id)
                                self.assertIsNotNone(process.returncode)
                                break
                            (project / (phase + '-release')).touch()
                            if outcome == 'extract_failed': break
                        await asyncio.wait_for(task, 10)
                        final = await call(operation='read', run_id=run_id, offset=0, wait_seconds=0)
                        self.assertEqual(final['run']['status'], 'interrupted' if outcome.startswith('interrupted_') else 'completed')
                        self.assertEqual(final['run']['exit_code'], 0)
                        self.assertTrue(final['run']['ended'])
                        if not outcome.startswith('interrupted_'):
                            self.assertEqual(final['run']['coverage_status'], 'merged' if outcome == 'merged' else 'failed')
                        if outcome.endswith('_failed'):
                            self.assertIn('exit 2', final['run']['coverage_error'])
                        self.assertEqual(len(state(project)['runs']), 1 if outcome == 'merged' else 0)
                        self.assertNotIn(run_id, service.test_runs.processes)
                        empty = await call(operation='read', run_id=run_id, offset=final['offset'], wait_seconds=0)
                        self.assertEqual(empty['text'], '')
                        self.assertEqual(len(service.test_runs.list(project)), 1)
                        print('coverage lifecycle:', json.dumps({'outcome': outcome, 'during': during,
                            'final_status': final['run']['status'], 'exit_code': final['run']['exit_code'],
                            'coverage_status': final['run']['coverage_status'], 'ended': bool(final['run']['ended']),
                            'published_runs': len(state(project)['runs'])}), flush=True)
                finally:
                    await service.test_runs.close()
                    await service.close()
