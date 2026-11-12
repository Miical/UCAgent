"""Overlay panel acceptance on native stages without maintaining a second workflow."""
import ast
import copy
import json
from pathlib import Path

from ucagent.checkers.base import Checker
from .planning_targets import PlanningTargetsChecker
from .targets import LOCK
from .testcases import Cases


def panel_stages(stages, rules):
    """Preserve native topology and validators while adding scoped panel integration."""
    stages = copy.deepcopy(stages)
    levels = {"dut_function_grouping": "FG", "function_point_definition": "FC",
              "check_point_design": "CK", "functional_line_mapping_gap_analysis": "CK",
              "functional_specification_refine": "CK"}
    coverage = {"coverage_group_creation", "coverage_point_implementation",
                "implemente_function_checks_in_batch"}
    case_stages = {"create_test_case_templates", "comprehensive_verification_and_bug_analysis",
                   "refine_test_cases_based_on_functional_points", "static_bug_validation",
                   "generate_random_test_cases", "verification_review_and_summary", "record_and_report_bugs"}
    def visit(rows, tests=False):
        """Apply additions recursively without introducing parent or child nodes."""
        for row in rows:
            name = row["name"]
            tests = tests or name == "test_environment_implementation"
            tasks = [rules["instruction"]]
            if name == "requirement_analysis_and_planning":
                tasks.append(rules["planning"])
            if name in coverage or name == "coverage_model_implementation" or tests:
                tasks.append(rules["implementation"])
            if tests:
                tasks.append(rules["cases"])
            row["task"] = tasks + row.get("task", [])
            args = None
            if name == "requirement_analysis_and_planning":
                args = {"mode": "plan"}
            elif name in levels:
                args = {"mode": "targets", "leaf": levels[name]}
            elif name in coverage:
                args = {"mode": "targets", "leaf": "FG" if name == "coverage_group_creation" else "CK",
                        "bindings": True, "complete_only": name == "implemente_function_checks_in_batch"}
            elif name in case_stages:
                args = {"mode": "final" if name in {"verification_review_and_summary", "record_and_report_bugs"} else "cases"}
            if args:
                row["checker"] = [{
                    "name": "flowcanvas_panel", "clss": "flowcanvas.default_workflow.DefaultPanelChecker",
                    "args": {**args, "doc_file": "{OUT}/{DUT}_functions_and_checks.md",
                             "plan_file": "{OUT}/{DUT}_verification_needs_and_plan.md",
                             "test_dir": "{OUT}/tests",
                             "published_dir": "{PROJECT_ROOT}/verification/default/{DUT}/tests",
                             "cov_file": "{OUT}/tests/{DUT}_function_coverage_def.py"},
                }] + row.get("checker", [])
            visit(row.get("stage", []), tests)
    visit(stages)
    return stages


class DefaultPanelChecker(Checker):
    """Require project cards to describe the actual native verified implementation."""

    def __init__(self, mode, doc_file, plan_file, test_dir, published_dir, cov_file,
                 leaf="CK", bindings=False, complete_only=False, **kwargs):
        """Store stage requirements without performing workspace I/O."""
        super().__init__()
        if mode not in {"plan", "targets", "cases", "final"}:
            raise ValueError("Unknown default panel acceptance mode")
        self.mode, self.doc_file, self.plan_file = mode, doc_file, plan_file
        self.test_dir, self.published_dir, self.cov_file = test_dir, published_dir, cov_file
        self.leaf, self.bindings, self.complete_only = leaf, bindings, complete_only

    def do_check(self, **kwargs):
        """Generate native indexes and reject absent, stale or mismatched panel records."""
        if self.complete_only and not kwargs.get("is_complete", False):
            return True, {"panel_check": "deferred_until_complete"}
        try:
            with LOCK:
                request = json.loads((Path(self.workspace).parent / "request.json").read_text())
                root = Path(request["project_root"]).resolve()
                if self.mode in {"plan", "targets", "final"}:
                    checker = PlanningTargetsChecker(
                        self.doc_file, leaf="FG" if self.mode == "plan" else self.leaf,
                        bindings=self.bindings or self.mode == "final",
                        cov_file=self.cov_file if self.bindings or self.mode == "final" else None,
                        published_cov_file=str(Path(self.published_dir) / Path(self.cov_file).name))
                    checker.workspace = self.workspace
                    ok, result = checker.do_check(**kwargs)
                    if not ok:
                        return ok, result
                    if self.mode == "plan":
                        Path(self.get_path(self.plan_file)).write_text(Path(self.get_path(self.doc_file)).read_text())
                    if self.mode != "final":
                        return True, result
                store = Cases(root)
                definition = store.read()
                published = Path(self.published_dir).resolve()
                if not published.is_relative_to(root):
                    raise ValueError("Published tests must remain inside the project")
                native = Path(self.get_path(self.test_dir))
                sources = sorted(p for p in native.rglob("*.py") if "__pycache__" not in p.parts)
                if not sources:
                    raise ValueError("Native workflow has no Python test sources")
                files = []
                for source in sources:
                    target = published / source.relative_to(native)
                    if not target.resolve().is_relative_to(root):
                        raise ValueError("Published source escapes the project")
                    if ast.dump(ast.parse(source.read_text())) != ast.dump(ast.parse(target.read_text())):
                        raise ValueError("Published source differs from native checked code: " + source.name)
                    files.append(str(target.relative_to(root)))
                published_files = {str(p.relative_to(root)) for p in published.rglob("*.py")
                                   if "__pycache__" not in p.parts}
                if published_files != set(files):
                    raise ValueError("Published Python source inventory differs from native checked code")
                expected = {}
                for file in files:
                    path=root/file
                    if not (path.name.startswith('test_') or path.name.endswith('_test.py')):
                        continue
                    tree=ast.parse(path.read_text())
                    for node in tree.body:
                        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test_'):
                            expected[(file,node.name)] = node
                        if isinstance(node,ast.ClassDef) and node.name.startswith('Test'):
                            for method in node.body:
                                if isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)) and method.name.startswith('test_'):
                                    expected[(file,node.name+'::'+method.name)] = method
                if not expected:
                    raise ValueError("No native pytest cases found")
                reports = store.status()['reports']
                seen = set()
                for case in definition.targets:
                    report=reports[case.id]
                    if case.lifecycle!='ready' or report['status']!='bound' or not case.title.strip() or not case.description.strip():
                        raise ValueError('Case needs a finished card and current source binding: '+case.id)
                    for binding in report['bindings']:
                        if binding['role']!='definition':
                            continue
                        key=(binding['file'],case.name.replace('.', '::'))
                        if key in expected:
                            node=expected[key]
                            first=min([node.lineno]+[d.lineno for d in node.decorator_list])
                            if binding['line']>first or binding['end_line']<node.end_lineno:
                                raise ValueError('Definition binding must contain the complete test: '+case.id)
                            if key in seen:
                                raise ValueError('Native test has multiple cards: '+str(key))
                            seen.add(key)
                        elif Path(binding['file']).is_relative_to(published.relative_to(root)):
                            raise ValueError('Obsolete card refers to a removed native test: '+case.id)
                if seen != set(expected):
                    raise ValueError('Native tests missing panel cards: '+str(sorted(set(expected)-seen)[:15]))
                if any(case.runner is None for case in definition.targets):
                    raise ValueError("Configure a runner on every test case before accepting tests")
                if self.mode == "final" and not definition.coverage:
                    raise ValueError("Configure real per-run coverage extraction before final acceptance")
                return True, {"panel_tests": len(seen), "published_files": len(files)}
        except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
            return False, {"error_code": "DEFAULT_PANEL_MISMATCH", "error": str(exc),
                           "next_action": "Update original panel cards and publish native checked sources; "
                                          "inspect, bind and finish operations through FlowCanvas tools, then retry."}
