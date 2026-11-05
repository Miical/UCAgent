"""Planning workflow checks reuse UCAgent and require live workbench bindings."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from flowcanvas.chat import ChatService
from flowcanvas.goal_planning import GoalPlanningChecker
from flowcanvas.targets import Targets
from flowcanvas.workflow_runtime import WorkflowRuntime


class GoalPlanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_host_and_model_without_dut(self):
        """Verify document/code/UI reconciliation and reject accidental test integration."""
        asyncio.get_running_loop().set_debug(False)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'project'
            project.mkdir()
            rtl = project / 'Probe.v'
            rtl.write_text('module Probe(input a, output y); assign y=a; endmodule')
            service = ChatService(Path(directory) / 'state')
            service.projects.state['current'] = {'path': str(project)}
            owner = service.create()
            owner['status'] = 'running'
            runtime = WorkflowRuntime(service, owner)
            try:
                started = await runtime.run('plan-goals', str(rtl), 'Probe')
                card = started['card']
                self.assertIn('tools', started, (Path(card['root']) / 'host.log').read_text()[-3000:])
                work = Path(card['workspace'])
                self.assertTrue((work / 'Guide_Doc/dut_functions_and_checks.md').is_file())
                self.assertTrue((work / 'Guide_Doc/dut_function_coverage_def.md').is_file())
                relative = 'verification/coverage/Probe'
                output = project / relative
                output.mkdir(parents=True)
                for name in ('requirements.md', 'design.md', 'README.md'):
                    (output / name).write_text('# Probe\nCoverage definition only; sample from later tests.\n')
                (output / 'functions_and_checks.md').write_text(
                    '# Probe\n\n<FG-INPUT>\nInput domain\n\n<FC-INPUT>\nInput state\n\n<CK-ONE>\nInput high\n')
                (output / 'coverage.py').write_text(
                    'from toffee.funcov import CovGroup\n'
                    'def bin_one(dut):\n    return dut.a.value == 1\n'
                    'def add_point(group, dut):\n    group.add_watch_point(dut, {"CK-ONE": bin_one}, name="FC-INPUT")\n'
                    'def make_group(dut):\n    group=CovGroup("FG-INPUT")\n    add_point(group,dut)\n    return group\n'
                    'def get_coverage_groups(dut):\n    return [make_group(dut)]\n')
                (output / 'selftest.py').write_text(
                    'from types import SimpleNamespace\nfrom coverage import bin_one, get_coverage_groups\n'
                    'dut=SimpleNamespace(a=SimpleNamespace(value=0))\n'
                    'assert not bin_one(dut)\ndut.a.value=1\nassert bin_one(dut)\n'
                    'assert len(get_coverage_groups(dut))==1\n')
                def check(phase):
                    checker = GoalPlanningChecker(str(project), relative, phase)
                    checker.workspace = str(work)
                    return checker.do_check()
                self.assertTrue(check('understand')[0])
                self.assertTrue(check('define')[0], check('define'))
                self.assertFalse(check('refine')[0])
                store = Targets(project)
                mapping = {}
                parent = None
                entries = [('group','FG-INPUT','make_group'),('point','FG-INPUT/FC-INPUT','add_point'),
                           ('testpoint','FG-INPUT/FC-INPUT/CK-ONE','bin_one')]
                for kind, tag, function in entries:
                    row = store.create(kind, tag, parent, store.read().revision, description='Input observation')
                    mapping[tag] = row['id']
                    parent = row['id']
                    nodes = store.inspect([relative + '/coverage.py'])[0]['nodes']
                    node = next(n for n in nodes if n['name'] == function)
                    store.bind(row['id'], {'file': relative + '/coverage.py', 'start': node['start'],
                               'fingerprint': node['fingerprint'], 'role': 'coverage'}, store.read().revision)
                (output / 'target-map.json').write_text(json.dumps(mapping))
                self.assertTrue(check('refine')[0], check('refine'))
                self.assertTrue(check('implement')[0], check('implement'))
                self.assertTrue(check('verify')[0], check('verify'))
                for phase in range(5):
                    await runtime.invoke(card['id'], 'SetCurrentStageJournal', {'journal': 'Checked docs, target bindings and synthetic coverage model.'})
                    await runtime.invoke(card['id'], 'Complete', {})
                async with asyncio.timeout(10):
                    while not (Path(card['root']) / 'complete.json').exists():
                        await asyncio.sleep(.1)
                self.assertEqual(len(card['stages']), 6)

                (project / 'test_accidental.py').write_text('# unwanted test integration')
                self.assertFalse(check('verify')[0])
                (project / 'test_accidental.py').unlink()
                (output / 'selftest.py').write_text('raise AssertionError("bad sampling")')
                self.assertFalse(check('verify')[0])
            finally:
                await runtime.close()
                await service.close()
