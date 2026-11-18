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
                self.assertIn('已有产物校验候选：no', started['instruction'])
                self.assertIn('RunBashCommand', [t['name'] for t in started['tools']])
                checker = PickerPackageChecker()
                checker.workspace = card['workspace']
                self.assertFalse(checker.do_check()[0])
                self.assertFalse(checker.do_check(reuse_existing=True)[0])
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
                reused = await runtime.run('picker-compile', str(rtl), 'Probe')
                self.assertIn('已有产物校验候选：yes', reused['instruction'])
                reuse_card = reused['card']
                reuse_checker = PickerPackageChecker()
                reuse_checker.workspace = reuse_card['workspace']
                self.assertFalse(reuse_checker.do_check()[0])
                await runtime.invoke(reuse_card['id'], 'SetCurrentStageJournal',
                                     {'journal': 'Reuse existing Probe package after checking current RTL.'})
                await runtime.invoke(reuse_card['id'], 'Complete', {'stage_args': {'reuse_existing': True}})
                async with asyncio.timeout(10):
                    while not (Path(reuse_card['root']) / 'complete.json').exists():
                        await asyncio.sleep(.1)
                evidence = json.loads((Path(reuse_card['workspace']) / 'summary/picker-result.json').read_text())
                self.assertTrue(evidence['reused'])
                await runtime.invoke(reuse_card['id'], 'Exit', {})

                planning = await runtime.run('plan-goals', str(rtl), 'Probe')
                plan_card = planning['card']
                plan_workspace = Path(plan_card['workspace'])
                self.assertFalse((plan_workspace / 'Probe/__init__.py').exists())
                tips = await runtime.invoke(plan_card['id'], 'CurrentTips', {})
                self.assertIn('已有产物校验候选：yes', str(tips['result']))
                await runtime.invoke(plan_card['id'], 'SetCurrentStageJournal',
                                     {'journal': 'Reused the verified Probe package in planning.'})
                accepted = await runtime.invoke(plan_card['id'], 'Complete',
                                                {'stage_args': {'reuse_existing': True}})
                self.assertIn('planning_dut', str(accepted['result']), accepted['result'])
                async with asyncio.timeout(10):
                    while True:
                        progress = json.loads((Path(plan_card['root']) / 'progress.json').read_text())
                        if progress['current_stage'] == 'requirement_analysis_and_planning':
                            break
                        await asyncio.sleep(.1)
                self.assertTrue(next(s for s in progress['stages'] if s['name'] == 'picker_compile')['completed'])
                self.assertTrue((plan_workspace / 'Probe/__init__.py').exists())
                self.assertTrue(json.loads((plan_workspace / 'summary/picker-result.json').read_text())['reused'])

                wrapper = package / '__init__.py'
                original = wrapper.read_bytes()
                wrapper.write_bytes(original + b'\n# modified wrapper\n')
                self.assertFalse(reuse_checker.do_check(reuse_existing=True)[0])
                wrapper.write_bytes(original)

                rtl.write_text(before.decode() + '// changed\n')
                self.assertFalse(reuse_checker.do_check(reuse_existing=True)[0])
                changed = await runtime.run('picker-compile', str(rtl), 'Probe')
                self.assertIn('已有产物校验候选：no', changed['instruction'])
                reuse_checker.workspace = changed['card']['workspace']
                self.assertFalse(reuse_checker.do_check(reuse_existing=True)[0])
            finally:
                await runtime.close()
                await service.close()
