"""Static acceptance for incremental single-test authoring, without DUT execution."""
import ast
import json
import shlex
from pathlib import Path

from ucagent.checkers.base import Checker
from .targets import Targets
from .testcases import Cases


def command_option(command, option):
    """Read a literal CLI option without executing the command or its substitutions."""
    tokens = shlex.split(command)
    for index, token in enumerate(tokens):
        if token.startswith(option + "="):
            return token[len(option)+1:]
        if token == option and index+1 < len(tokens):
            return tokens[index+1]
    return None


class TestcaseAuthoringChecker(Checker):
    """Tie one newly reserved project case to real source and native authoring conventions."""

    def __init__(self, mode="ready", **kwargs):
        """Select reservation, implementation or final panel acceptance."""
        super().__init__()
        if mode not in {"reserved", "implemented", "ready"}:
            raise ValueError("Unknown authoring acceptance mode")
        self.mode = mode

    def do_check(self, **kwargs):
        """Inspect persisted records and Python AST only; never import project modules."""
        try:
            work = Path(self.workspace)
            request = json.loads((work.parent / "request.json").read_text())
            root = Path(request["project_root"]).resolve()
            plan = json.loads((work / "summary/testcase_plan.json").read_text())
            store = Cases(root)
            definition = store.read()
            case = next((row for row in definition.targets if row.id == plan["case_id"]), None)
            if case is None or case.id in request["testcase_baseline"]:
                raise ValueError("Plan must identify a newly reserved case, not a preexisting case")
            if not case.title.strip() or not case.description.strip():
                raise ValueError("Case needs a readable title and source-grounded description")
            if self.mode != "ready" and (case.lifecycle != "creating" or
                    case.operation_session != request["conversation_id"] or case.operation_status != "running"):
                raise ValueError("Resume the original creating case before continuing this workflow")
            if self.mode == "ready" and case.lifecycle != "ready":
                raise ValueError("Call finish_create on the original case after source validation")
            files = plan.get("files")
            targets = plan.get("target_ids")
            if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
                raise ValueError("Plan files must list project-relative implementation paths")
            if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
                raise ValueError("Plan target_ids must be a list of actual testpoint IDs")
            for file in files:
                path = (root / file).resolve()
                if Path(file).is_absolute() or not path.is_relative_to(root):
                    raise ValueError("Implementation paths must remain in the project")
            if self.mode == "reserved":
                return True, {"case_id": case.id, "state": "reserved"}
            if not definition.runner:
                raise ValueError("Configure a real project runner before finishing authoring")
            coverage_plan = plan.get("coverage_plan") or {}
            if not definition.coverage or coverage_plan.get("adapter") not in {"toffee", "normalized"}:
                raise ValueError("Configure coverage extraction and record coverage_plan.adapter")
            if "{run_dir}" not in definition.runner.command:
                raise ValueError("Run outputs must be isolated using {run_dir}")
            for field in ("line_source", "functional_source"):
                if not isinstance(coverage_plan.get(field), str) or "{run_dir}" not in coverage_plan[field]:
                    raise ValueError("coverage_plan needs per-run " + field)
            if coverage_plan.get("line_enabled") is not True or coverage_plan.get("functional_enabled") is not True:
                raise ValueError("Both line and functional collection must be enabled; unavailable does not complete authoring")
            collectors = coverage_plan.get("collector_files")
            if not isinstance(collectors, list) or not collectors:
                raise ValueError("List actual collector_files in coverage_plan")
            collection_calls = set()
            for file in collectors:
                if not isinstance(file, str) or Path(file).is_absolute():
                    raise ValueError("Collector paths must be project-relative")
                path = (root / file).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError("Missing collector source")
                if path.suffix == ".py":
                    for node in ast.walk(ast.parse(path.read_text())):
                        if isinstance(node, ast.Call):
                            if isinstance(node.func, ast.Name):
                                collection_calls.add(node.func.id)
                            elif isinstance(node.func, ast.Attribute):
                                collection_calls.add(node.func.attr)
            if coverage_plan["adapter"] == "toffee":
                needed = {"SetCoverage", "set_line_coverage", "set_func_coverage"}
                if not needed <= collection_calls:
                    raise ValueError("Missing Toffee collection calls: " + ", ".join(sorted(needed-collection_calls)))
                extract = definition.coverage.extract_command
                if "--dat-dir" not in extract and "--lcov" not in extract:
                    raise ValueError("Toffee extraction must convert current-run .dat with --dat-dir or supply --lcov")
                report_dir = command_option(definition.runner.command, "--report-dir")
                if "--toffee-report" in shlex.split(definition.runner.command) and "flowcanvas.coverage_adapter" in extract:
                    report_source = command_option(extract, "--report")
                    if not report_dir or report_dir.rstrip("/") != (report_source or "").rstrip("/"):
                        raise ValueError("Pass the same per-run directory to --report and --report-dir; "
                                         "Toffee creates a timestamped report subdirectory")

            for field in ("mapping_file", "build_inputs_file"):
                value = coverage_plan.get(field)
                if not isinstance(value, str) or Path(value).is_absolute():
                    raise ValueError("coverage_plan needs project-relative " + field)
                path = (root / value).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError("Missing coverage plan input: " + field)
                payload = json.loads(path.read_text())
                if field == "mapping_file":
                    if not isinstance(payload, dict) or not set(targets) <= set(payload.values()):
                        raise ValueError("Coverage map must include each selected testpoint ID")
                else:
                    from .coverage_adapter import identity
                    identity(root, path)
            for file in files:
                path = root / file
                if not path.is_file():
                    raise ValueError("Missing implementation file: " + file)
                if path.suffix == ".py":
                    ast.parse(path.read_text(), filename=file)
            report = store.status()["reports"][case.id]
            if report["status"] != "bound":
                raise ValueError("All case source bindings must be current and bound")
            bindings = [b for b in case.bindings if b.role == "definition"]
            if len(bindings) != 1 or bindings[0].file not in files:
                raise ValueError("Bind exactly one test function definition included in plan files")
            binding = bindings[0]
            source = store.source(binding.id)
            tree = ast.parse(store.file(binding.file).read_text())
            name = case.name.split(".")[-1]
            matches = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and node.name == name and node.lineno >= source["line"] and node.end_lineno <= source["end_line"]]
            if len(matches) != 1:
                raise ValueError("The registered entry must resolve to its bound top-level pytest function")
            function = matches[0]
            dut = request["dut"]
            if not Path(binding.file).name.startswith("test_" + dut + "_") or not name.startswith("test_") or len(name) <= 5:
                raise ValueError("Use test_<DUT>_<feature>.py and test_<scenario> for a directed case")
            if name.startswith(("test_api_", "test_static_", "test_random_")):
                raise ValueError("Reserved API/static/random namespaces are not ordinary directed tests")
            if not ast.get_docstring(function):
                raise ValueError("Document the test scenario, stimulus and expected behavior")
            args = [arg.arg for arg in function.args.posonlyargs + function.args.args]
            config_path = root / ".ucagent/runtime_config.json"
            config = json.loads(config_path.read_text()) if config_path.is_file() else {}
            need_ref = config.get("runtime_options", {}).get("need_ref_model", False)
            expected = ["env", "ref_model"] if need_ref else ["env"]
            if args[:len(expected)] != expected:
                raise ValueError("Test fixtures must start with " + ", ".join(expected))
            if not any(isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == dut + "_api"
                       and any(alias.name == "*" for alias in node.names) for node in tree.body):
                raise ValueError("Import project APIs and fixtures using from <DUT>_api import *")
            assertions = [node for node in ast.walk(function) if isinstance(node, ast.Assert)]
            if not assertions or any(isinstance(n.test, ast.Constant) for n in assertions):
                raise ValueError("Implement real behavioral assertions; no constant assert placeholders")
            if any(isinstance(node, ast.Attribute) and node.attr in {"skip", "skipif", "xfail"}
                   for node in ast.walk(function)):
                raise ValueError("A new directed case must not bypass validation with skip or xfail")
            calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
            if not any(isinstance(c.func, ast.Name) and c.func.id.startswith("api_" + dut + "_") for c in calls):
                raise ValueError("Reuse or incrementally implement a real api_<DUT>_* operation")
            marks = [c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "mark_function"]
            if not marks or not targets:
                raise ValueError("Associate this case with real testpoints through mark_function")
            labels = set()
            for call in marks:
                if (len(call.args) != 3 or not isinstance(call.args[1], ast.Name) or call.args[1].id != name
                        or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str)
                        or not call.args[0].value.startswith("FC-")
                        or not isinstance(call.args[2], (ast.List, ast.Tuple)) or not call.args[2].elts):
                    raise ValueError("mark_function needs literal FC/CK labels and this test function object")
                receiver = call.func.value
                if (not isinstance(receiver, ast.Subscript) or not isinstance(receiver.value, ast.Attribute)
                        or receiver.value.attr != "fc_cover" or not isinstance(receiver.slice, ast.Constant)
                        or not isinstance(receiver.slice.value, str) or not receiver.slice.value.startswith("FG-")):
                    raise ValueError("mark_function must use the real fc_cover[FG] group")
                for item in call.args[2].elts:
                    if not isinstance(item, ast.Constant) or not isinstance(item.value, str) or not item.value.startswith("CK-"):
                        raise ValueError("Use actual literal CK labels in mark_function")
                    labels.add(item.value)
            target_store = Targets(root)
            target_report = target_store.status()
            by_id = {target["id"]: target for target in target_report["targets"]}
            known_labels = set()
            for identity in targets:
                target = by_id.get(identity)
                if not target or target["kind"] != "testpoint" or target["lifecycle"] != "ready":
                    raise ValueError("Finish registering the selected testpoint: " + identity)
                chain = [target, by_id[target["parent"]], by_id[by_id[target["parent"]]["parent"]]]
                for item in chain:
                    if item["lifecycle"] != "ready" or target_report["reports"][item["id"]]["status"] != "bound":
                        raise ValueError("Selected target hierarchy needs valid source bindings: " + item["id"])
                    for record in item["bindings"]:
                        file = target_store.file(record["file"])
                        if file.suffix == ".py":
                            known_labels.update(node.value for node in ast.walk(ast.parse(file.read_text()))
                                                if isinstance(node, ast.Constant) and isinstance(node.value, str))
            if not labels <= known_labels:
                raise ValueError("Marked CK labels are absent from selected targets' real coverage source")
            return True, {"case_id": case.id, "files": files, "testpoints": targets,
                          "acceptance": "static_only", "simulation_executed": False}
        except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
            return False, {"error_code": "TESTCASE_AUTHORING_INCOMPLETE", "error": str(exc),
                           "next_action": "Repair the original case, its minimal dependencies and source bindings. "
                                          "Follow Guide_Doc/dut_test_case.md; do not run DUT tests or create a duplicate case."}
