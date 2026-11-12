"""Incremental case acceptance uses actual panel bindings without executing user code."""
from target_test_support import inspect_fixture, bind_target, reserve_case, bind_case

import json
from pathlib import Path
import pytest
from flowcanvas.targets import Targets
from flowcanvas.testcases import Cases, RunTemplate, CoverageConfig
from flowcanvas.testcase_authoring import TestcaseAuthoringChecker as AuthoringChecker


@pytest.fixture
def authored(tmp_path):
    """Create one scoped project, native-style source and three real coverage bindings."""
    root=tmp_path/"project"
    root.mkdir()
    work=tmp_path/"task/workspace"
    (work/"summary").mkdir(parents=True)
    request={"project_root":str(root),"dut":"Probe","conversation_id":"owner","testcase_baseline":[]}
    request_path=work.parent/"request.json"
    request_path.write_text(json.dumps(request))
    cases=Cases(root)
    case=reserve_case(cases,"test_zero",{"title":"Zero operands","description":"Check zero input sum"},0,"owner")
    definition=cases.read()
    definition.targets[0].runner=RunTemplate(command="pytest --report-dir={run_dir}/report {case}",description="Existing project pytest runner")
    definition.coverage=CoverageConfig(tool="toffee-verilator",description="Native report and LCOV",
        artifact_path="{run_dir}/coverage.json",extract_command="python extract.py --dat-dir {run_dir}/raw",
        merge_command="python merge.py {inputs_manifest} {merge_dir}",
        report_path="{merge_dir}/coverage.json",build_id_command="python identity.py")
    cases.save(definition,definition.revision)
    source=root/"verification/tests/test_Probe_zero.py"
    source.parent.mkdir(parents=True)
    source.write_text("""from Probe_api import *
raise RuntimeError("This file must never execute during acceptance")

def test_zero(env):
    """ + '"""Check a zero input operation against the independently known result."""' + """
    env.dut.fc_cover["FG-API"].mark_function("FC-ADD", test_zero, ["CK-ZERO"])
    actual = api_Probe_add(env, 0, 0)
    assert actual == 0
""")
    api=source.parent/"Probe_api.py"
    api.write_text("from toffee_test.reporter import set_func_coverage, set_line_coverage\n"
                   "def api_Probe_add(env, a, b, max_cycles=100):\n    return env.add(a, b)\n"
                   "def collect(request, dut, groups, path):\n"
                   "    dut.SetCoverage(path)\n"
                   "    set_func_coverage(request, groups)\n"
                   "    set_line_coverage(request, path, dut=dut)\n")
    file=str(source.relative_to(root))
    bind_case(cases,case['id'],file,'test_zero',owner='owner',finish=False)
    targets=Targets(root)
    parent=None
    ids=[]
    coverage=root/"verification/coverage/Probe/Probe_function_coverage_def.py"
    coverage.parent.mkdir(parents=True)
    coverage.write_text('group_label = "FG-API"\npoint_label = "FC-ADD"\ncheck_label = "CK-ZERO"\n')
    cov_file=str(coverage.relative_to(root))
    for kind,name in (("group","group_label"),("point","point_label"),("testpoint","check_label")):
        row=targets.create(kind,name,parent,targets.read().revision,description="Scenario behavior")
        node=next(n for n in inspect_fixture(targets,[cov_file])[0]["nodes"] if n["name"].startswith(name))
        bind_target(targets,row["id"],{"file":cov_file,"start":node["start"],"fingerprint":node["fingerprint"],"role":"coverage"},
                     targets.read().revision)
        parent=row["id"];ids.append(parent)
    mapping=root/"verification/coverage/map.json"
    mapping.write_text(json.dumps({"FG-API/FC-ADD/CK-ZERO":ids[-1]}))
    inputs=root/"verification/coverage/build-inputs.json"
    inputs.write_text(json.dumps([cov_file]))
    plan={"case_id":case["id"],"target_ids":[ids[-1]],"files":[file,str(api.relative_to(root))],
          "coverage_plan":{"adapter":"toffee","line_enabled":True,"functional_enabled":True,
                           "collector_files":[str(api.relative_to(root))],"line_source":"{run_dir}/coverage.dat",
                           "functional_source":"{run_dir}/report/toffee_report.json",
                           "mapping_file":str(mapping.relative_to(root)),
                           "build_inputs_file":str(inputs.relative_to(root))}}
    plan_path=work/"summary/testcase_plan.json"
    plan_path.write_text(json.dumps(plan))
    def check(mode="implemented"):
        """Run only the new deterministic static gate."""
        checker=AuthoringChecker(mode=mode)
        checker.workspace=str(work)
        return checker.do_check()
    return root,work,cases,case["id"],source,plan_path,request_path,check


def test_scoped_authoring_accepts_real_bindings_without_importing(authored):
    """A missing full environment never blocks static authoring or executes a DUT."""
    root,work,cases,identity,source,plan,request,check=authored
    assert check("reserved")[0]
    ok,result=check()
    assert ok,result
    assert result["simulation_executed"] is False
    assert check("ready")[0] is False
    __import__('flowcanvas.testcases',fromlist=['execute']).execute(root,dict(operation='end_modify',ids=[identity],expected_revision=cases.read().revision),'owner')
    assert check("ready")[0]


def test_old_case_wrong_owner_escape_and_missing_dependency_are_rejected(authored):
    """Reject substituted records and files that do not belong to this project."""
    root,work,cases,identity,source,plan_path,request_path,check=authored
    request=json.loads(request_path.read_text())
    request["testcase_baseline"]=[identity]
    request_path.write_text(json.dumps(request))
    assert not check()[0]
    request["testcase_baseline"]=[]
    request["conversation_id"]="other"
    request_path.write_text(json.dumps(request))
    assert not check("reserved")[0]
    request["conversation_id"]="owner"
    request_path.write_text(json.dumps(request))
    plan=json.loads(plan_path.read_text())
    original=list(plan["files"])
    for file in ("../outside.py","absent.py"):
        plan["files"]=original+[file]
        plan_path.write_text(json.dumps(plan))
        assert not check()[0]


@pytest.mark.parametrize("before,after",[
    ("assert actual == 0","assert True"),
    ("assert actual == 0","assert False"),
    ("assert actual == 0","pass"),
    ("test_zero(env)","test_zero(dut)"),
    ("from Probe_api import *","from Probe_api import env"),
    ('["CK-ZERO"]','["CK-UNKNOWN"]'),
    ("api_Probe_add(env, 0, 0)","42"),
    ("def test_zero(env):","@pytest.mark.skip\ndef test_zero(env):"),
])
def test_invalid_case_contract_is_rejected_after_binding_accept(authored,before,after):
    """A fresh binding cannot hide an invalid native test contract."""
    root,work,cases,identity,source,plan,request,check=authored
    source.write_text(source.read_text().replace(before,after))
    assert not check()[0]
    reports=cases.status()["reports"][identity]["bindings"]
    if reports[0].get("fingerprint"):
        cases.accept(identity,cases.read().revision,{r["id"]:r["fingerprint"] for r in reports})
    assert not check()[0]


def test_reference_configuration_and_missing_target_binding_are_enforced(authored):
    """Respect resolved project fixture configuration and actual target states."""
    root,work,cases,identity,source,plan,request,check=authored
    (root/".ucagent").mkdir()
    (root/".ucagent/runtime_config.json").write_text(json.dumps({"runtime_options":{"need_ref_model":True}}))
    assert not check()[0]
    source.write_text(source.read_text().replace("test_zero(env)","test_zero(env, ref_model)"))
    report=cases.status()["reports"][identity]["bindings"][0]
    cases.accept(identity,cases.read().revision,{report["id"]:report["fingerprint"]})
    assert check()[0]
    (root/"verification/coverage/Probe/Probe_function_coverage_def.py").unlink()
    assert not check()[0]


@pytest.mark.asyncio
async def test_native_host_loads_four_incremental_stages_and_guides(tmp_path):
    """New authoring hosts receive actual project identity and baseline without a model."""
    import asyncio
    from flowcanvas.chat import ChatService
    from flowcanvas.workflow_runtime import WorkflowRuntime
    asyncio.get_running_loop().set_debug(False)
    project=tmp_path/"project"
    project.mkdir()
    rtl=project/"Probe.v"
    rtl.write_text("module Probe(input a, output y); assign y=a; endmodule")
    service=ChatService(tmp_path/"state")
    service.projects.state["current"]={"path":str(project)}
    owner=service.create()
    owner["status"]="running"
    runtime=WorkflowRuntime(service,owner)
    try:
        result=await runtime.run("generate-testcase",str(rtl),"Probe")
        card=result["card"]
        assert "tools" in result,(Path(card["root"])/"host.log").read_text()[-3000:]
        stages=[s["name"] for s in card["stages"]]
        assert stages[1:]==["select_and_reserve_testcase","implement_case_and_dependencies",
                            "review_case_contract","finalize_testcase"]
        request=json.loads((Path(card["root"])/"request.json").read_text())
        assert request["project_root"]==str(project)
        assert request["conversation_id"]==owner["id"]
        assert request["testcase_baseline"]==[]
        guide=Path(card["workspace"])/"Guide_Doc/dut_test_case.md"
        assert guide.is_file()
        assert "mark_function" in guide.read_text()
    finally:
        await runtime.close()
        await service.close()


def test_authoring_requires_a_saved_per_run_extraction_plan(authored):
    """Neither a test entry nor an empty coverage config completes authoring."""
    root,work,cases,identity,source,plan_path,request_path,check=authored
    plan=json.loads(plan_path.read_text())
    del plan["coverage_plan"]
    plan_path.write_text(json.dumps(plan))
    assert not check()[0]


@pytest.mark.parametrize("change",["disabled","missing_setcoverage","missing_line_report","no_lcov"])
def test_unavailable_cannot_bypass_dual_collection_setup(authored,change):
    """Reject plans that claim completion while omitting part of either collection chain."""
    root,work,cases,identity,source,plan_path,request_path,check=authored
    plan=json.loads(plan_path.read_text())
    api=root/plan["coverage_plan"]["collector_files"][0]
    if change=="disabled":
        plan["coverage_plan"]["line_enabled"]=False
        plan_path.write_text(json.dumps(plan))
    elif change=="missing_setcoverage":
        api.write_text(api.read_text().replace("dut.SetCoverage(path)","pass"))
    elif change=="missing_line_report":
        api.write_text(api.read_text().replace("set_line_coverage(request, path, dut=dut)","pass"))
    else:
        data=cases.read()
        data.coverage.extract_command="python functional_only.py"
        cases.save(data,data.revision)
    assert not check()[0]


def test_report_directory_must_match_toffee_output_contract(authored):
    """Creation rejects assuming JSON is directly below Toffee's report root."""
    root,work,cases,identity,source,plan_path,request_path,check=authored
    data=cases.read()
    data.targets[0].runner.command="pytest {case} --toffee-report --report-dir={run_dir}/toffee"
    data.coverage.extract_command="python -m flowcanvas.coverage_adapter extract --dat-dir {run_dir}/raw --report {run_dir}/toffee/toffee_report.json"
    cases.save(data,data.revision)
    ok,result=check()
    assert not ok and "timestamped" in result["error"]
    data=cases.read()
    data.coverage.extract_command=data.coverage.extract_command.replace("/toffee/toffee_report.json","/toffee")
    cases.save(data,data.revision)
    assert check()[0]
