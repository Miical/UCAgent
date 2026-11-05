"""Real Picker compilation and workflow acceptance using a disposable RTL."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from flowcanvas.chat import ChatService
from flowcanvas.picker_build import PickerPackageChecker
from flowcanvas.workflow_runtime import WorkflowRuntime


@unittest.skipUnless(shutil.which('picker') and shutil.which('verilator'), 'Requires Picker and Verilator')
class PickerWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_compile_external_rtl_and_accept_package(self):
        """Keep external source intact, reject missing build and accept a loadable package."""
        asyncio.get_running_loop().set_debug(False)
        with tempfile.TemporaryDirectory(prefix='flowcanvas-picker-') as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            rtl = root / 'Probe.v'
            rtl.write_text('module Probe(input a, output y); assign y = a; endmodule\n')
            before = rtl.read_bytes()
            service = ChatService(root / 'state')
            service.projects.state['current'] = {'path': str(project)}
            owner = service.create()
            owner['status'] = 'running'
            runtime = WorkflowRuntime(service, owner)
            try:
                started = await runtime.run('picker-compile', str(rtl), 'Probe')
                card = started['card']
                request = Path(card['root']) / 'request.json'
                self.assertIn('tools', started, (Path(card['root']) / 'host.log').read_text()[-4500:])
                self.assertIn('RunBashCommand', [t['name'] for t in started['tools']])
                checker = PickerPackageChecker()
                checker.workspace = card['workspace']
                self.assertFalse(checker.do_check()[0])
                command = ['picker', 'export', str(rtl), '--sname', 'Probe', '--sim', 'verilator',
                           '--rw', '1', '--lang', 'python', '--tdir', str(project / 'verification') + '/',
                           '-c', '-w', 'Probe.fst', '-j', '4']
                await asyncio.to_thread(subprocess.run, command, check=True, timeout=180,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                package = project / 'verification/Probe'
                self.assertEqual(rtl.read_bytes(), before)
                self.assertTrue(checker.do_check()[0])
                spec = json.loads(request.read_text())
                original_start = spec['build_started_at']
                spec['build_started_at'] = 9999999999
                request.write_text(json.dumps(spec))
                self.assertFalse(checker.do_check()[0])
                self.assertTrue(checker.do_check(reuse_existing=True)[0])
                self.assertFalse(checker.do_check(reuse_existing='true')[0])
                spec['build_started_at'] = original_start
                request.write_text(json.dumps(spec))

                evidence = json.loads((Path(card['workspace']) / 'summary/picker-result.json').read_text())
                self.assertEqual(evidence['output'], str(package))
                await runtime.invoke(card['id'], 'SetCurrentStageJournal', {'journal': 'Compiled external Probe RTL and instantiated DUTProbe.'})
                await runtime.invoke(card['id'], 'Complete', {})
                async with asyncio.timeout(10):
                    while not (Path(card['root']) / 'complete.json').exists():
                        await asyncio.sleep(.1)
                await runtime.invoke(card['id'], 'Exit', {})
                # Changed inputs cannot reuse the old completion evidence.
                rtl.write_text(before.decode() + '// changed\n')
                self.assertFalse(checker.do_check()[0])
            finally:
                await runtime.close()
                await service.close()
