"""Automatic continuation resumes only explicitly started, unfinished workflows."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from flowcanvas.chat import ChatService

class WorkflowContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.service=ChatService(Path(self.temp.name)/"state")
        self.owner=self.service.create()
        self.owner.update(status="completed",model="model",effort="low",turn="turn-one",
                          ended_at=10,queue_paused=False,delegation_stopped=False)
        self.card={"id":"card","root":self.temp.name,"status":"paused","auto_continue":True}
        self.owner["workflow_runs"]=[self.card]
        self.service.launch=Mock()
    def tearDown(self):
        self.temp.cleanup()
    def run_continue(self):
        with patch("flowcanvas.chat.time.time",return_value=20):
            return self.service.continue_workflows(self.owner)
    def test_resumes_same_card_and_preserves_model_settings_once(self):
        self.assertTrue(self.run_continue())
        owner,message,tier=self.service.launch.call_args.args
        self.assertIs(owner,self.owner)
        self.assertIn('"card"',message.text)
        self.assertEqual(message.model,"model")
        self.assertEqual(message.effort,"low")
        self.assertEqual(message.workflow_context,{})
        self.assertFalse(self.run_continue())
    def test_stop_error_children_queue_and_busy_states_block_continuation(self):
        for fields in ({"delegation_stopped":True},{"queue_paused":True},
                       {"children_running":1},{"children_pending":True},
                       {"queue":[{}]},{"status":"running"},{"status":"interrupted"},
                       {"status":"error"},{"model":""}):
            with self.subTest(fields=fields):
                before=dict(self.owner)
                self.owner.update(fields)
                self.assertFalse(self.run_continue())
                self.owner.clear();self.owner.update(before)
        self.assertFalse(self.service.launch.called)
    def test_completed_receipt_and_unarmed_history_do_not_restart(self):
        for fields in ({"status":"completed"},{"dismissed":True},{"auto_continue":False}):
            before=dict(self.card);self.card.update(fields)
            self.assertFalse(self.run_continue())
            self.card.clear();self.card.update(before)
        (Path(self.temp.name)/"complete.json").write_text("{}")
        self.assertFalse(self.run_continue())
    def test_new_terminal_turn_can_continue_but_launch_and_steering_ownership_block(self):
        self.service.launching.add(self.owner["id"]);self.assertFalse(self.run_continue())
        self.service.launching.clear();self.service.steering.add(self.owner["id"])
        self.assertFalse(self.run_continue())
        self.service.steering.clear();self.assertTrue(self.run_continue())
        self.owner["turn"]="turn-two";self.assertTrue(self.run_continue())
        self.assertEqual(self.service.launch.call_count,2)
    def test_restart_does_not_reactivate_persisted_tasks(self):
        self.service.save(self.owner)
        restored=ChatService(self.service.root)
        owner=restored.sessions[self.owner["id"]]
        self.assertTrue(owner["delegation_stopped"])
        self.assertFalse(owner["workflow_runs"][0]["auto_continue"])
    def test_user_stop_or_backend_error_disarms_the_workflow(self):
        self.owner["thread"]="native"
        self.owner["status"]="running"
        self.service.event({"kind":"turn","thread":"native","turn":"turn-one","status":"interrupted","error":""})
        self.assertFalse(self.card["auto_continue"])


class WorkflowContinuationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_starts_a_real_service_turn_in_the_same_conversation(self):
        """The background scheduler sends a continuation using the existing native thread."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as directory:
            service=ChatService(Path(directory)/"state")
            owner=service.create()
            owner.update(status="completed",thread="native",turn="first",model="model",effort="low",
                         ended_at=1,queue_paused=False,delegation_stopped=False,runtime_permission_policy=2)
            owner["workflow_runs"]=[{"id":"card","root":directory,"status":"paused","auto_continue":True}]
            backend=SimpleNamespace(open=AsyncMock(return_value="native"),
                                    send=AsyncMock(return_value="second"),close=AsyncMock(),emit=lambda e:None)
            service.session_backends[owner["id"]]=backend
            service.flusher=asyncio.create_task(service.flush())
            try:
                async with asyncio.timeout(5):
                    while not backend.send.called:
                        await asyncio.sleep(.02)
                self.assertEqual(backend.open.call_args.args[0],"native")
                self.assertIn('"card"',backend.send.call_args.args[1])
                self.assertEqual(owner["status"],"starting")
                self.assertEqual(len(owner["items"]),1)
                self.assertEqual(owner["items"][0]["workflow_label"],"自动继续工作流")
            finally:
                await service.close()
