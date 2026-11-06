"""Selected planning stages remain identical to the upstream workflow."""
import re
import unittest
from pathlib import Path
import yaml
from ucagent.util.config import UCAgentConfigLoader, replace_bash_var
import os

class GoalPlanningTests(unittest.TestCase):
    def test_panel_gates_preserve_native_validation(self):
        """Every definition and implementation gate includes panel acceptance."""
        from importlib import import_module
        root=Path(__file__).resolve().parents[3]
        workflow=(root/"plugins/FlowCanvas/src/flowcanvas/workflows/plan-goals.yaml").read_text()
        selected=yaml.load(replace_bash_var(workflow,os.environ),Loader=UCAgentConfigLoader)
        names=["compile_rtl","requirement_analysis_and_planning","dut_function_understanding",
               "functional_specification_analysis","functional_specification_refine",
               "coverage_model_implementation"]
        self.assertEqual([s["name"] for s in selected["stage"]],names)
        gates=[]
        def visit(stages):
            """Walk nested stages and instantiate each newly configured checker."""
            for stage in stages:
                for checker in stage.get("checker",[]):
                    if checker["clss"]=="flowcanvas.planning_targets.PlanningTargetsChecker":
                        gates.append(stage["name"])
                        module,_,name=checker["clss"].rpartition(".")
                        instance=getattr(import_module(module),name)(**checker["args"])
                        self.assertTrue(instance.doc_file)
                self.assertNotIn("unitytest/functions-and-checks",stage.get("skill_list",[]))
                visit(stage.get("stage",[]))
        visit(selected["stage"])
        for enabled in ("true","false"):
            resolved=yaml.load(replace_bash_var(workflow,{**os.environ,"HUMAN_CHECK_CK":enabled}),
                               Loader=UCAgentConfigLoader)
            check_stage=resolved["stage"][3]["stage"][2]
            self.assertEqual(check_stage["need_human_check"],enabled=="true")
        self.assertIn("不因 README 缺失停止",workflow)
        self.assertEqual(len(gates),10)
        for name in ("dut_function_grouping","function_point_definition","check_point_design",
                     "coverage_model_implementation","implemente_function_checks_in_batch"):
            self.assertIn(name,gates)
        self.assertIn("唯一目标定义来源",workflow)
        self.assertIn("UnityChipCheckerCoverageGroupBatchImplementation",workflow)
        self.assertIn("UnityChipCheckerLabelStructureRefine",workflow)
        self.assertNotIn("创建文档框架",workflow)

class NativePlanningHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_host_requires_dut_package(self):
        """Start native checkers without a model and preserve missing-DUT prerequisites."""
        import asyncio
        import tempfile
        from flowcanvas.chat import ChatService
        from flowcanvas.workflow_runtime import WorkflowRuntime
        asyncio.get_running_loop().set_debug(False)
        with tempfile.TemporaryDirectory() as directory:
            project=Path(directory)/"project"
            project.mkdir()
            rtl=project/"Probe.v"
            rtl.write_text("module Probe(input a,output y);assign y=a;endmodule")
            service=ChatService(Path(directory)/"state")
            service.projects.state["current"]={"path":str(project)}
            owner=service.create()
            owner["status"]="running"
            runtime=WorkflowRuntime(service,owner)
            try:
                result=await runtime.run("plan-goals",str(rtl),"Probe")
                card=result["card"]
                self.assertIn("tools",result,(Path(card["root"])/"host.log").read_text()[-3000:])
                self.assertIn("compile_rtl",card["current_stage"])
                self.assertTrue(any(s.get("depth")==1 for s in card["stages"]))
                self.assertTrue(any(s.get("depth")==2 for s in card["stages"]))
                request=__import__("json").loads((Path(card["root"])/"request.json").read_text())
                self.assertNotIn("goal_output",request)
                self.assertNotIn("goal_baseline",request)
                self.assertFalse((Path(card["workspace"])/"Probe/__init__.py").exists())
            finally:
                await runtime.close()

class PlanningPackageTests(unittest.TestCase):
    def test_accepted_package_is_installed_without_rewriting_identical_rtl(self):
        """Expose accepted native files while preserving staged RTL permissions."""
        import json
        import tempfile
        from unittest.mock import patch
        from flowcanvas.picker_build import PickerPackageChecker
        from flowcanvas.planning_compile import PlanningPickerChecker
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            work=root/"workspace"
            target=work/"Probe"
            target.mkdir(parents=True)
            staged=target/"Probe.v"
            staged.write_text("module Probe;endmodule")
            staged.chmod(0o444)
            package=root/"project/verification/Probe"
            package.mkdir(parents=True)
            (package/"Probe.v").write_bytes(staged.read_bytes())
            (package/"__init__.py").write_text("# compiled wrapper")
            (package/"_UT_Probe.so").write_bytes(b"fixture")
            (root/"request.json").write_text(json.dumps({"dut":"Probe","dut_output":str(package)}))
            checker=PlanningPickerChecker()
            checker.workspace=str(work)
            with patch.object(PickerPackageChecker,"do_check",return_value=(True,{"load_check":"passed"})):
                ok,result=checker.do_check()
            self.assertTrue(ok,result)
            self.assertEqual((target/"_UT_Probe.so").read_bytes(),b"fixture")
            self.assertEqual(staged.stat().st_mode & 0o777,0o444)
            with patch.object(PickerPackageChecker,"do_check",return_value=(False,{"error":"not accepted"})):
                self.assertFalse(checker.do_check()[0])

    def test_deferred_gate_still_checks_when_planning_begins(self):
        """Attaching before compilation is allowed; entering the stage validates the files."""
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        from flowcanvas.planning_compile import DeferredDutFilesChecker
        with tempfile.TemporaryDirectory() as directory:
            checker=DeferredDutFilesChecker(files=["Probe/__init__.py"])
            checker.workspace=directory
            manager=SimpleNamespace(agent=SimpleNamespace(exit=lambda:None))
            checker.set_stage_manager(manager)
            with patch("flowcanvas.planning_compile.OrginFileMustExistChecker.set_stage_manager",
                       side_effect=ValueError("missing DUT")) as validate:
                with self.assertRaisesRegex(ValueError,"missing DUT"):
                    checker.on_init()
                validate.assert_called_once_with(checker,manager)
