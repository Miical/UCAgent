"""Acceptance gates for FlowCanvas test-suite authoring."""
import ast
import json
from pathlib import Path

from ucagent.checkers.base import Checker, UnityChipBatchTask

from .run_results import test_result
from .smoke_runs import case_identity, managed_run, validate_coverage_smoke_receipt
from .testcase_authoring import TestcaseAuthoringChecker
from .testcases import Cases
from .targets import Targets


def project_files(store, files):
    """Resolve a nonempty list of existing project files."""
    if not isinstance(files, list) or not files:
        raise ValueError("列出实际使用的项目相对源码路径")
    if any(not isinstance(file, str) or Path(file).is_absolute() for file in files):
        raise ValueError("使用项目相对源码路径")
    return [store.file(file) for file in files]


def test_function(store, card):
    """Resolve the exact top-level pytest definition inside the card's source binding."""
    entries = [binding for binding in card.bindings if binding.role == "definition"]
    if len(entries) != 1 or Path(entries[0].file).suffix != ".py":
        raise ValueError(f"{card.id} 需要绑定一个 Python 测试入口")
    binding = entries[0]
    source = store.source(binding.id)
    tree = ast.parse(store.file(binding.file).read_text())
    for function in tree.body:
        if (isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                function.name == card.name and
                source["line"] <= function.lineno <= function.end_lineno <= source["end_line"]):
            return function
    raise ValueError(f"{card.id} 的源码绑定未包含登记的函数 {card.name}")


def ready_card(store, cards, reports, identity):
    card = cards.get(identity)
    if card is None or card.lifecycle != "ready" or reports[identity]["status"] != "bound":
        raise ValueError(f"完成用例 {identity} 的登记并核对源码绑定")
    store.validate_card(card)
    return card


def require_fixture(files, name):
    """Verify that a listed source declares the fixture with pytest's decorator."""
    for file in files:
        if file.suffix != ".py":
            continue
        for function in ast.parse(file.read_text()).body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in function.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if not (isinstance(target, ast.Name) and target.id == "fixture" or
                        isinstance(target, ast.Attribute) and target.attr == "fixture"):
                    continue
                explicit = next((item.value for item in decorator.keywords if item.arg == "name"), None) if isinstance(decorator, ast.Call) else None
                fixture_name = explicit.value if isinstance(explicit, ast.Constant) else function.name
                if fixture_name == name and any(not isinstance(node, ast.Pass) for node in function.body):
                    return
    raise ValueError(f"列出的源码必须实际定义 pytest fixture {name}")


class TestSuiteChecker(Checker):
    """Check reusable fixtures, panel-backed tests and a current smoke receipt."""

    def __init__(self, mode, batch_size=10, **kwargs):
        super().__init__()
        if mode not in {"dut", "environment", "api", "templates", "implementation", "smoke"}:
            raise ValueError("Unknown test-suite gate")
        self.mode = mode
        if mode == "implementation":
            self.batch_size = int(batch_size)
            self.batch = UnityChipBatchTask("test_cases", self)

    def do_check(self, is_complete=False, **kwargs):
        """Accept only project source, current panel cards and managed run receipts."""
        try:
            workspace = Path(self.workspace)
            request = json.loads((workspace.parent/"request.json").read_text())
            store = Cases(request["project_root"])
            output = workspace/"summary"
            environment = json.loads((output/"test-environment.json").read_text())
            fixtures = self.fixtures(store, environment)
            if self.mode in {"dut", "environment"}:
                return True, {"sources": [file.relative_to(store.root).as_posix() for file in fixtures]}
            if self.mode == "api":
                self.api_checks(store, environment, fixtures, request["dut"])
                return True, {"validated_api_runs": len(environment["api_runs"])}
            plan = json.loads((output/"test-suite.json").read_text())
            if self.mode == "smoke":
                return True, self.smoke(store, plan)
            return self.cases(store, request, plan, is_complete)
        except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
            return False, {"error_code": "TEST_SUITE_INCOMPLETE", "error": str(exc),
                           "next_action": "核对当前阶段的实际源码、面板卡片或运行收据，修复对应项后重试。"}

    def fixtures(self, store, environment):
        dut_files = project_files(store, environment["dut_files"])
        require_fixture(dut_files, "dut")
        if self.mode == "dut":
            return dut_files
        env_files = project_files(store, environment["environment_files"])
        require_fixture(env_files, "env")
        runtime = store.root/".ucagent/runtime_config.json"
        options = json.loads(runtime.read_text()).get("runtime_options", {}) if runtime.is_file() else {}
        if options.get("mock_components_enabled"):
            require_fixture(env_files, "mock_dut")
        return dut_files + env_files

    def api_checks(self, store, environment, fixtures, dut):
        receipts = environment["api_runs"]
        if not isinstance(receipts, list) or not receipts:
            raise ValueError("api_runs 需要列出基础 API 测试的卡片 ID 和实际运行 ID")
        definition = store.read()
        cards = {card.id: card for card in definition.targets}
        reports = store.status()["reports"]
        latest_fixture = max(file.stat().st_mtime for file in fixtures)
        for receipt in receipts:
            card = ready_card(store, cards, reports, receipt["case_id"])
            function = test_function(store, card)
            calls = [node.func for node in ast.walk(function) if isinstance(node, ast.Call)]
            if (not function.name.startswith(f"test_api_{dut}_") or
                    not any(isinstance(node, ast.Assert) and not isinstance(node.test, ast.Constant)
                            for node in ast.walk(function)) or
                    not any(isinstance(fn, ast.Name) and fn.id.startswith(f"api_{dut}_") for fn in calls)):
                raise ValueError(f"{card.id} 需要驱动实际基础 API 并核对可观测结果")
            run, log = managed_run(store.root, receipt["run_id"])
            if (run.get("case_id") != card.id or run.get("case_identity") != case_identity(card) or
                    (run.get("ended") or 0) < latest_fixture or test_result(run, log)["status"] != "passed"):
                raise ValueError(f"{card.id} 缺少匹配当前源码、fixture 和运行入口的通过记录")

    def cases(self, store, request, plan, is_complete):
        entries = plan["cases"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("test-suite.json 需要至少一个已规划的普通用例")
        ids = [entry["case_id"] for entry in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("每个用例 ID 在计划中只能出现一次")
        data = store.read()
        cards = {card.id: card for card in data.targets}
        reports = store.status()["reports"]
        targets = {target.id for target in Targets(store.root).read().targets if target.kind == "testpoint"}

        reserved = TestcaseAuthoringChecker(mode="reserved")
        ready = TestcaseAuthoringChecker(mode="ready")
        accepted, pending = [], {}
        for entry in entries:
            identity = entry["case_id"]
            card = cards[identity]
            target_ids = entry["target_ids"]
            if not isinstance(target_ids, list) or not target_ids or not set(target_ids) <= targets:
                raise ValueError(f"{identity} 需要关联面板中真实存在的测试点")
            entry = {**entry, "coverage_plan": plan.get("coverage_plan")}
            if self.mode == "templates":
                if card.lifecycle == "ready":
                    test_function(store, ready_card(store, cards, reports, identity))
                else:
                    passed, result = reserved.check_plan(request, entry)
                    if not passed:
                        raise ValueError(f"{identity}: {result['error']}")
                    self.template(store, card, entry, request["dut"])
                continue
            passed, result = ready.check_plan(request, entry, allow_existing=True)
            if passed:
                accepted.append(identity)
            else:
                pending[identity] = result["error"]
        if self.mode == "templates":
            return True, {"planned_cases": len(entries)}

        notes = []
        self.batch.sync_source_task(ids, notes, "用例计划发生变化")
        self.batch.sync_gen_task(accepted, notes, "面板验收结果发生变化")
        passed, result = self.batch.do_complete(
            notes, is_complete, "test-suite.json 的用例 ID", "FlowCanvas 用例卡片",
            "实现当前批次，更新原卡片的源码绑定和运行配置后调用 end_modify。",
        )
        if isinstance(result, dict):
            result["case_errors"] = {key: pending[key] for key in self.batch.tbd_task_list if key in pending}
        return passed, result

    @staticmethod
    def template(store, card, entry, dut):
        for file in project_files(store, entry["files"]):
            if not file.name.startswith(f"test_{dut}_") or file.suffix != ".py":
                continue
            for node in ast.parse(file.read_text()).body:
                if (isinstance(node, ast.FunctionDef) and node.name == card.name and
                        ast.get_docstring(node) and any(isinstance(n, ast.Assert) and
                        isinstance(n.test, ast.Constant) and n.test.value is False for n in node.body)):
                    return
        raise ValueError(f"{card.id} 缺少与计划一致、含场景说明和 assert False 的模板")

    @staticmethod
    def smoke(store, plan):
        definition = store.read()
        cards = {card.id: card for card in definition.targets}
        reports = store.status()["reports"]
        smoke_id = plan["smoke_case_id"]
        reference_id = plan["smoke_reference_case_id"]
        planned = {entry["case_id"] for entry in plan["cases"]}
        if smoke_id in planned or reference_id not in planned:
            raise ValueError("使用独立冒烟用例，并从本次普通用例选择一个作为运行参考")
        smoke = ready_card(store, cards, reports, smoke_id)
        reference = ready_card(store, cards, reports, reference_id)
        function = test_function(store, smoke)
        if not any(isinstance(node, ast.Assert) and not isinstance(node.test, ast.Constant)
                   for node in ast.walk(function)):
            raise ValueError("冒烟用例需要实际检查可观测行为")
        report = validate_coverage_smoke_receipt(
            store.root, smoke, reference, definition.coverage, plan["smoke_run_id"])
        return {"planned_cases": len(planned), "smoke_case_id": smoke.id,
                "smoke_run_id": plan["smoke_run_id"], "line_status": report["line_status"]}
