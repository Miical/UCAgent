"""Workflow stage durations count execution, not paused host wall time."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from flowcanvas.workflow_runtime import WorkflowRuntime


class WorkflowClockTests(unittest.TestCase):
    def test_pause_resume_and_stage_transition(self):
        card = {'id': 'run', 'status': 'running', 'current_stage': 'compile',
                'stages': [{'name': 'compile', 'completed': False, 'time_cost': '05m 17s'}]}
        runtime = WorkflowRuntime(SimpleNamespace(), {'workflow_runs': [card]})
        def tick(now):
            with patch('flowcanvas.workflow_runtime.time.monotonic', return_value=now):
                runtime.update_running_time()
        tick(10)
        tick(12)
        card['status'] = 'paused'
        tick(13)
        self.assertEqual(card['stages'][0]['time_cost'], '320s')
        card['stages'][0]['time_cost'] = '15m 00s'  # A fresh host snapshot includes pause time.
        tick(100)
        self.assertEqual(card['stages'][0]['time_cost'], '320s')
        card['status'] = 'running'
        tick(101)
        tick(103)
        self.assertEqual(card['stages'][0]['time_cost'], '322s')
        card['stages'][0]['completed'] = True
        card['current_stage'] = 'load'
        card['stages'].append({'name': 'load', 'completed': False, 'time_cost': '0s'})
        tick(104)
        tick(106)
        self.assertEqual(card['stages'][0]['time_cost'], '323s')
        self.assertEqual(card['stages'][1]['time_cost'], '2s')


    def test_nested_parent_clocks_and_pause(self):
        """Ancestors run with their active descendant and stop accumulating while paused."""
        rows=[{'name':'leaf','parent':'middle','time_cost':'0s'},
              {'name':'sibling','parent':'middle','time_cost':'0s'},
              {'name':'middle','parent':'parent','time_cost':'0s'},
              {'name':'parent','parent':None,'time_cost':'0s'}]
        card={'id':'run','status':'running','current_stage':'leaf','stages':rows}
        runtime=WorkflowRuntime(SimpleNamespace(),{'workflow_runs':[card]})
        def tick(now):
            with patch('flowcanvas.workflow_runtime.time.monotonic',return_value=now):
                runtime.update_running_time()
        tick(10);tick(15)
        self.assertEqual([r['time_cost'] for r in rows],['5s','0s','5s','5s'])
        self.assertEqual([r['in_progress'] for r in rows],[True,False,True,True])
        self.assertFalse(any(r.get('completed') for r in rows))
        card['status']='paused';tick(16);tick(100)
        self.assertEqual(rows[-1]['time_cost'],'6s')
        card['status']='running';rows[0]['completed']=True
        card['current_stage']='sibling';tick(101);tick(104)
        self.assertEqual([r['time_cost'] for r in rows],['6s','3s','9s','9s'])
        rows[1]['completed']=True;card['current_stage']='middle';tick(105);tick(107)
        self.assertEqual(rows[2]['time_cost'],'12s')
        self.assertEqual(rows[-1]['time_cost'],'12s')
        rows[2]['completed']=True;card['current_stage']='parent';tick(108);tick(110)
        self.assertFalse(rows[2]['in_progress'])
        self.assertTrue(rows[-1]['in_progress'])
        self.assertEqual(rows[-1]['time_cost'],'15s')


class WorkflowProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_kills_child_process_group(self):
        """A compiler ignoring TERM must not survive pausing its MCP host."""
        import asyncio
        import json
        import sys
        import tempfile
        from pathlib import Path
        import psutil
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / 'child'
            child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(120)"
            code = ("import subprocess,sys,time; from pathlib import Path; "
                    "p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); "
                    "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(120)")
            proc = await asyncio.create_subprocess_exec(sys.executable, '-c', code,
                        str(pidfile), child, start_new_session=True)
            card = {'id': 'run', 'status': 'running', 'stages': [], 'root': directory}
            service = SimpleNamespace(changed=Mock(), save=Mock())
            runtime = WorkflowRuntime(service, {'workflow_runs': [card]})
            runtime.processes['run'] = proc
            try:
                async with asyncio.timeout(5):
                    while not pidfile.exists():
                        await asyncio.sleep(.02)
                pid = int(pidfile.read_text())
                await asyncio.sleep(.1)
                await runtime.pause()
                self.assertEqual(card['status'], 'paused')
                self.assertFalse(card['alive'])
                async with asyncio.timeout(5):
                    while psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                        await asyncio.sleep(.02)
                await runtime.pause()  # Repeated cleanup is harmless.
            finally:
                await runtime.close()
