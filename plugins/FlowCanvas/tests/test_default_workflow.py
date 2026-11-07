"""Regression coverage for minimal native-workflow overlays and real panel acceptance."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ucagent.util.config import load_yaml_with_env_vars
from flowcanvas.default_workflow import DefaultPanelChecker, panel_stages
from flowcanvas.testcases import Cases, RunTemplate


class NativeOverlayTests(unittest.TestCase):
    """Keep original stages, skills, skips and checkers intact."""

    def test_native_tree_preserved_for_runtime_variants(self):
        """Only panel tasks/checkers are added for each native configuration."""
        root = Path(__file__).resolve().parents[3]
        overlay = root / "plugins/FlowCanvas/src/flowcanvas/workflows/default.yaml"
        for enabled in ("true", "false"):
            with patch.dict("os.environ", {"HUMAN_CHECK_CK": enabled,
                                          "NEED_REF_MODEL": enabled,
                                          "MOCK_COMPONENTS_ENABLED": enabled}):
                native = load_yaml_with_env_vars(root / "ucagent/lang/zh/config/default.yaml")["stage"]
                before = copy.deepcopy(native)
                result = panel_stages(native, load_yaml_with_env_vars(overlay)["flowcanvas_panel"])
                self.assertEqual(native, before)
                self.assertEqual(len(result), 20)
                def compare(left, right):
                    """Compare all native data except the explicitly extended fields."""
                    self.assertEqual(len(left), len(right))
                    for old, new in zip(left, right):
                        for key, value in old.items():
                            if key not in {"stage", "task", "checker"}:
                                self.assertEqual(new[key], value)
                        original_checks = old.get("checker", [])
                        if original_checks:
                            self.assertEqual(new["checker"][-len(original_checks):], original_checks)
                        # Never turn an unchecked container into another executable stage.
                        if not original_checks:
                            self.assertEqual(new.get("checker", []), [])
                        self.assertEqual(new["task"][-len(old["task"]):], old["task"])
                        compare(old.get("stage", []), new.get("stage", []))
                compare(native, result)


class PublishedCaseTests(unittest.TestCase):
    """Exercise source reconciliation with the actual Cases store and parser."""

    def setUp(self):
        """Create one native test and its project publication."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.work = self.root / "workspace"
        self.native = self.work / "summary/tests"
        self.native.mkdir(parents=True)
        self.published = self.project / "verification/default/Probe/tests"
        self.published.mkdir(parents=True)
        self.source = "def test_result():\n    assert 1 + 1 == 2\n"
        (self.native / "test_probe.py").write_text(self.source)
        (self.published / "test_probe.py").write_text(self.source)
        (self.root / "request.json").write_text(json.dumps({"project_root": str(self.project), "dut": "Probe"}))
        self.store = Cases(self.project)
        data = self.store.read()
        data.runner = RunTemplate(command='python -m pytest "{case}"', description="pytest")
        self.store.save(data, data.revision)
        self.checker = DefaultPanelChecker("cases", "summary/Probe_functions_and_checks.md",
                                          "summary/Probe_verification_needs_and_plan.md",
                                          "summary/tests", str(self.published), "summary/tests/Probe_function_coverage_def.py")
        self.checker.workspace = str(self.work)

    def register(self):
        """Import one real independently runnable test using public discovery."""
        file = str((self.published / "test_probe.py").relative_to(self.project))
        candidate = self.store.discover([file])["cases"][0]
        return self.store.import_files([file], self.store.read().revision, [{
            "file": file, "start": candidate["start"], "fingerprint": candidate["fingerprint"],
            "reason": "Independent pytest test", "title": "Arithmetic result",
            "description": "Check the expected result",
        }])

    def test_obsolete_published_source_is_rejected(self):
        """Removing a native test must not leave a runnable published ghost test."""
        self.register()
        (self.published / "test_old.py").write_text("def test_old():\n    assert True\n")
        ok, result = self.checker.do_check()
        self.assertFalse(ok)
        self.assertIn("inventory differs", result["error"])

    def test_missing_binding_and_native_source_mismatch(self):
        """A document or card cannot substitute for the tested implementation."""
        self.assertFalse(self.checker.do_check()[0])
        self.register()
        ok, result = self.checker.do_check()
        self.assertTrue(ok, result)
        # Binding annotations are permitted; semantic source changes are not.
        ast.parse((self.published / "test_probe.py").read_text())
        (self.native / "test_probe.py").write_text(self.source.replace("== 2", "== 3"))
        ok, result = self.checker.do_check()
        self.assertFalse(ok)
        self.assertIn("differs", result["error"])
        (self.native / "test_probe.py").write_text(self.source)
        data = self.store.read()
        data.targets[0].lifecycle = "updating"
        self.store.save(data, data.revision)
        self.assertFalse(self.checker.do_check()[0])


class NativeHostTests(unittest.IsolatedAsyncioTestCase):
    """Start the real native MCP host without invoking a model or simulation."""

    async def test_default_host_uses_bound_project(self):
        """Expose the full native tree with an existing package and panel instructions."""
        from flowcanvas.chat import ChatService
        from flowcanvas.workflow_runtime import WorkflowRuntime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            package = project / "verification/Probe"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("# compiled package fixture\n")
            rtl = project / "Probe.v"
            rtl.write_text("module Probe(input a, output y);assign y=a;endmodule")
            service = ChatService(root / "state")
            service.projects.state["current"] = {"path": str(project)}
            owner = service.create()
            owner["status"] = "running"
            runtime = WorkflowRuntime(service, owner)
            try:
                response = await runtime.run("default", str(rtl), "Probe")
                card = response["card"]
                log = (Path(card["root"]) / "host.log").read_text()
                self.assertIn("tools", response, log[-5000:])
                request = json.loads((Path(card["root"]) / "request.json").read_text())
                self.assertEqual(request["project_root"], str(project))
                self.assertEqual(request["workflow_id"], "default")
                self.assertIn("requirement_analysis_and_planning", card["current_stage"])
                self.assertTrue(any(s["name"] == "record_and_report_bugs" for s in card["stages"]))
                self.assertTrue((Path(card["workspace"]) / "Probe/__init__.py").is_file())
            finally:
                await runtime.close()
