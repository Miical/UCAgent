"""The six-stage suite uses real panel cards, fixture source and batch gates."""
import json

from flowcanvas.plugin import get_plugin
from flowcanvas.test_suite_workflow import TestSuiteChecker as SuiteChecker
from flowcanvas.testcases import execute
from test_testcase_authoring import authored  # noqa: F401
from ucagent.util.config import load_yaml_with_env_vars


def test_suite_is_registered_with_six_gates():
    workflow = next(item for item in get_plugin().workflows if item.name == "build-test-suite")
    config = load_yaml_with_env_vars(workflow.config_file)
    assert [stage["name"] for stage in config["stage"]] == [
        "dut_wrapper_implementation", "test_environment_implementation",
        "basic_api_functional_test", "create_test_case_templates",
        "implement_test_cases_in_batches", "validate_coverage_smoke",
    ]
    assert "Check" in config["tools"]["selected_tools"]
    for stage in config["stage"]:
        for gate in stage["checker"]:
            SuiteChecker(**gate["args"])


def test_suite_uses_panel_template_and_waits_until_registration(authored):
    root, work, store, case_id, source, plan_path, _, _ = authored
    fixtures = source.parent / "conftest.py"
    fixtures.write_text(
        "import pytest\n@pytest.fixture\ndef dut():\n    return make_dut()\n"
        "@pytest.fixture\ndef env(dut):\n    return wrap(dut)\n"
    )
    relative = fixtures.relative_to(root).as_posix()
    (work/"summary/test-environment.json").write_text(
        json.dumps({"dut_files": [relative], "environment_files": [relative]})
    )
    case_plan = json.loads(plan_path.read_text())
    coverage_plan = case_plan.pop("coverage_plan")
    (work/"summary/test-suite.json").write_text(json.dumps({
        "cases": [case_plan], "coverage_plan": coverage_plan,
    }))

    def gate(mode):
        checker = SuiteChecker(mode=mode, batch_size=1)
        checker.workspace = str(work)
        return checker

    assert gate("dut").do_check()[0]
    assert gate("environment").do_check()[0]
    runtime = root/".ucagent"
    runtime.mkdir()
    (runtime/"runtime_config.json").write_text(json.dumps({"runtime_options": {"mock_components_enabled": True}}))
    assert not gate("environment").do_check()[0]
    (runtime/"runtime_config.json").unlink()
    implementation = gate("implementation")
    real_source = source.read_text()
    source.write_text(real_source.replace("assert actual == 0", "assert False"))
    assert gate("templates").do_check()[0]
    assert not implementation.do_check()[0]
    source.write_text(real_source)
    execute(root, {"operation": "end_modify", "ids": [case_id], "expected_revision": store.read().revision}, "owner")
    passed, result = implementation.do_check(is_complete=True)
    assert passed, result
