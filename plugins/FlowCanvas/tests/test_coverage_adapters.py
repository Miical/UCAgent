"""Adapter interoperability, exact merges and source-bound publication."""
from target_test_support import inspect_fixture, bind_target

import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from flowcanvas.coverage_adapter import toffee_report, merge_reports, identity
from flowcanvas.coverage_schema import validate_report
from flowcanvas.targets import Targets, atomic


@pytest.fixture
def coverage_project(tmp_path):
    """Build real source bindings and native Toffee JSON inputs."""
    root=tmp_path/"project"
    root.mkdir()
    source=root/"Probe.v"
    source.write_text("module Probe(input a,output y);\nassign y=a;\nendmodule\n")
    (root/"coverage.py").write_text('group = "FG-API"\npoint = "FC-ADD"\ncheck = "CK-ZERO"\n')
    store=Targets(root)
    ids=[]
    for kind,name in (("group","group"),("point","point"),("testpoint","check")):
        row=store.create(kind,name,ids[-1] if ids else None,store.read().revision,description=name)
        node=next(n for n in inspect_fixture(store,["coverage.py"])[0]["nodes"] if n["name"].startswith(name+" ="))
        bind_target(store,row["id"],dict(file="coverage.py",start=node["start"],fingerprint=node["fingerprint"],role="coverage"),store.read().revision)
        ids.append(row["id"])
    mapping=root/"map.json"
    mapping.write_text(json.dumps({"FG-API/FC-ADD/CK-ZERO":ids[-1]}))
    def extract(name,hits,line_hits=None):
        """Normalize fixture observations using the production adapter."""
        report=root/(name+".json")
        report.write_text(json.dumps({"coverages":{"line":{} if line_hits is not None else None,
            "functional":{"groups":[{"name":"FG-API","points":[{"name":"FC-ADD",
                "bins":[{"name":"CK-ZERO","hints":hits}]}]}]}}}))
        lcov=None
        if line_hits is not None:
            lcov=root/(name+".info")
            lcov.write_text("SF:"+str(source)+"\n"+"".join(f"DA:{k},{v}\n" for k,v in line_hits.items())+"end_of_record\n")
        return toffee_report(root,report,"build-A",mapping,lcov,"Probe")
    return root,store,ids,extract


def test_exact_merge_and_python_panel_projection(coverage_project):
    """Merge line sets and bin counters rather than coverage percentages."""
    root,store,ids,extract=coverage_project
    paths=[]
    for index,report in enumerate([extract("one",0,{1:3,2:0}),extract("two",2,{1:0,2:4})]):
        path=root/f"normalized-{index}.json"
        atomic(path,report);paths.append(str(path))
    merged=merge_reports(paths)
    assert merged["instances"][0]["covered"]==2
    assert merged["instances"][0]["total"]==2
    assert merged["functional"]["observations"][-1]["bins"][0]["hits"]==2
    atomic(root/".flowcanvas/coverage/state.json",dict(epoch="one",runs=[{"id":"one"}],report=merged))
    assert all(store.status()["coverage"]["targets"][i]["status"]=="available" for i in ids)
    file=root/"coverage.py"
    file.write_text(file.read_text().replace('"CK-ZERO"','"CK-OTHER"'))
    assert store.status()["coverage"]["targets"][ids[-1]]["status"]=="stale"


def test_absent_line_data_and_measured_zero_remain_distinct(coverage_project):
    """Functional-only observations must not fabricate zero percent line coverage."""
    root,store,ids,extract=coverage_project
    report=extract("functional",0)
    assert report["line_status"]=="unavailable" and report["instances"]==[]
    assert report["functional"]["status"]=="available"
    assert report["functional"]["observations"][-1]["bins"][0]["hits"]==0


def test_invalid_counts_and_builds_fail_validation():
    """Reject malformed provider totals before publication."""
    report=dict(schema_version=1,build_id="A",instances=[dict(path="Probe",covered=1,total=2)])
    assert validate_report(report)["line_status"]=="available"
    for covered in (-1,True,3):
        with pytest.raises(ValueError):
            validate_report({**report,"instances":[dict(path="Probe",covered=covered,total=2)]})
    with pytest.raises(ValueError):validate_report(report,"B")


def test_incompatible_atoms_and_aggregate_only_merging_fail(coverage_project):
    """Counts alone cannot establish the union of two runs."""
    root,store,ids,extract=coverage_project
    one=extract("one",1,{1:1,2:0})
    two=extract("two",0,{1:0,2:1})
    two["instances"][0]["files"][0]["sha256"]="different"
    paths=[root/"one-normal.json",root/"two-normal.json"]
    atomic(paths[0],one);atomic(paths[1],two)
    with pytest.raises(ValueError,match="identities"):merge_reports(paths)
    two["instances"]=[dict(path="Probe",module="Probe",covered=1,total=2)]
    atomic(paths[1],two)
    with pytest.raises(ValueError,match="Exact line"):merge_reports(paths)


def test_build_identity_tracks_real_files_and_rejects_escape(coverage_project):
    """Build identity includes source content without reading outside the project."""
    root,store,ids,extract=coverage_project
    inputs=root/"inputs.json";inputs.write_text(json.dumps(["Probe.v","coverage.py"]))
    before=identity(root,inputs)
    (root/"Probe.v").write_text("module Probe; endmodule")
    assert identity(root,inputs)!=before
    inputs.write_text(json.dumps(["../outside"]))
    with pytest.raises(ValueError):identity(root,inputs)


def test_native_toffee_covgroup_serialization_is_supported(coverage_project):
    """Use real Toffee sampling and serialization, without a DUT or simulator."""
    from types import SimpleNamespace
    from toffee.funcov import CovGroup
    root,store,ids,extract=coverage_project
    group=CovGroup("FG-API")
    group.add_watch_point(SimpleNamespace(value=0),{"CK-ZERO":lambda signal:signal.value==0},name="FC-ADD")
    group.sample()
    report=root/"native-toffee.json"
    report.write_text(json.dumps({"coverages":{"functional":{"groups":[json.loads(str(group))]},"line":None}}))
    result=toffee_report(root,report,"build-A",root/"map.json")
    assert result["functional"]["observations"][-1]["bins"][0]["hits"]>0


@pytest.mark.asyncio
async def test_generic_source_preview_uses_hashed_snapshot(coverage_project):
    """Source preview works for any project and rejects escaped or changed source."""
    from types import SimpleNamespace
    from fastapi import FastAPI
    import httpx2
    from flowcanvas.test_runner import TestRuns, routes
    root,store,ids,extract=coverage_project
    report=extract("source",1,{1:1,2:0})
    atomic(root/".flowcanvas/coverage/state.json",dict(epoch="one",runs=[],report=report))
    projects=SimpleNamespace(state={"recent":[{"id":"project","path":str(root)}]})
    app=FastAPI()
    app.include_router(routes(projects,TestRuns()))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.get("/api/chat/projects/project/coverage/source",params={"instance":"Probe"})
        assert response.status_code==200,response.text
        original=response.json()["content"]
        (root/"Probe.v").write_text("module changed; endmodule")
        response=await client.get("/api/chat/projects/project/coverage/source",params={"instance":"Probe"})
        assert response.json()["content"]==original
        for file in (root/".flowcanvas/coverage/sources").iterdir():file.unlink()
        response=await client.get("/api/chat/projects/project/coverage/source",params={"instance":"Probe"})
        assert response.status_code==422


def test_native_database_conversion_uses_only_current_run(tmp_path,monkeypatch):
    """The adapter invokes the installed converter with only the requested run inputs."""
    from flowcanvas.coverage_adapter import convert_verilator_run
    import subprocess
    current=tmp_path/"current"
    current.mkdir()
    database=current/"one.dat"
    database.write_text("fixture")
    old=tmp_path/"old.dat"
    old.write_text("must not be included")
    output=tmp_path/"report.info"
    calls=[]
    def convert(args,**kwargs):
        """Model the converter output while checking the exact native invocation."""
        calls.append(args)
        output.write_text("SF:Probe.v\nDA:1,1\nend_of_record\n")
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(subprocess,"run",convert)
    assert convert_verilator_run(current,output,tmp_path,executable="/tools/verilator_coverage")==output
    assert calls==[["/tools/verilator_coverage","--write-info",str(output),str(database)]]
    database.unlink()
    with pytest.raises(ValueError,match="No Verilator"):
        convert_verilator_run(current,output,tmp_path,executable="/tools/verilator_coverage")
    database.symlink_to(old)
    with pytest.raises(ValueError,match="escapes"):
        convert_verilator_run(current,output,tmp_path,executable="/tools/verilator_coverage")


def test_toffee_timestamped_report_directory_is_explicit_and_unique(coverage_project):
    """Discover only the unique native report below the selected run directory."""
    root,store,ids,extract=coverage_project
    extract("original",1)
    report=root/"run/toffee/report-20261106/toffee_report.json"
    report.parent.mkdir(parents=True)
    report.write_bytes((root/"original.json").read_bytes())
    directory=root/"run/toffee"
    result=toffee_report(root,directory,"build-A",root/"map.json")
    assert result["functional"]["observations"]
    second=directory/"another/toffee_report.json"
    second.parent.mkdir()
    second.write_bytes(report.read_bytes())
    with pytest.raises(ValueError,match="exactly one"):
        toffee_report(root,directory,"build-A",root/"map.json")
    second.unlink()
    report.unlink()
    with pytest.raises(ValueError,match="found 0"):
        toffee_report(root,directory,"build-A",root/"map.json")
    report.symlink_to(root/"original.json")
    with pytest.raises(ValueError,match="escapes"):
        toffee_report(root,directory,"build-A",root/"map.json")


def test_valid_lcov_survives_optional_native_html_failure(coverage_project):
    """An HTML dependency failure does not invalidate independently observed line data."""
    root,store,ids,extract=coverage_project
    extract("html-failed",1,{1:2,2:0})
    report=root/"html-failed.json"
    raw=json.loads(report.read_text())
    raw["coverages"]["line"]["error"]="FileNotFoundError: genhtml"
    report.write_text(json.dumps(raw))
    adapted=toffee_report(root,report,"build-A",root/"map.json",root/"html-failed.info","Probe")
    assert adapted["instances"][0]["covered"]==1
    assert adapted["warnings"]
    with pytest.raises(ValueError,match="line collection failed"):
        toffee_report(root,report,"build-A",root/"map.json")


def test_native_types_and_generated_wrappers_do_not_pollute_line_coverage(coverage_project):
    """Only v_line atoms from original DUT sources contribute to the line ring."""
    from flowcanvas.coverage_adapter import native_verilator_metrics
    root,store,ids,extract=coverage_project
    wrapper=root/"Probe_top.sv"
    wrapper.write_text("module Probe_top; endmodule")
    (root/".flowcanvas/project.json").write_text(json.dumps({"rtl":{"files":["Probe.v"]}}))
    folder=root/"raw"
    folder.mkdir()
    def record(kind,file,line,hits):
        """Serialize an actual native Verilator coverage record."""
        return f"C '\x01f\x02{file}\x01l\x02{line}\x01n\x021\x01page\x02v_{kind}/Probe\x01h\x02TOP.Probe' {hits}\n"
    raw=folder/"coverage.dat"
    raw.write_text("# SystemC::Coverage-3\n"+record("line",root/"Probe.v",2,1)+
                   record("toggle",root/"Probe.v",1,0)+record("line",wrapper,1,0))
    rows,metrics=native_verilator_metrics(root,folder,"Probe")
    assert rows[0]["covered"]==rows[0]["total"]==1
    assert len(rows[0]["files"])==1
    assert metrics["line"]["generated"]["covered"]==0
    assert metrics["line"]["generated"]["total"]==1
    assert metrics["toggle"]["dut"]["total"]==1
    raw.write_text("# SystemC::Coverage-3\n"+record("toggle",root/"Probe.v",1,2)+record("line",wrapper,1,0))
    rows,metrics=native_verilator_metrics(root,folder,"Probe")
    assert rows==[]
    assert metrics["toggle"]["dut"]["covered"]==1


def test_corrected_measurement_cannot_mix_with_legacy_lcov(coverage_project):
    """Old mixed reports must be rebuilt, not silently merged with a new denominator."""
    root,store,ids,extract=coverage_project
    one=extract("old",1,{1:1})
    two=extract("new",1,{1:1})
    two["line_basis"]="verilator-native-line"
    paths=[root/"old-normal.json",root/"new-normal.json"]
    for path,report in zip(paths,(one,two)):atomic(path,report)
    with pytest.raises(ValueError,match="measurement changed"):
        merge_reports(paths)


@pytest.mark.asyncio
async def test_metric_source_preview_is_limited_to_reported_files(coverage_project):
    """Non-line metrics use the same hash-checked source endpoint, not arbitrary paths."""
    from types import SimpleNamespace
    from fastapi import FastAPI
    import httpx2
    import hashlib
    from flowcanvas.test_runner import TestRuns, routes
    root,store,ids,extract=coverage_project
    sha=hashlib.sha256((root/"Probe.v").read_bytes()).hexdigest()
    atomic(root/".flowcanvas/coverage/state.json",dict(epoch="one",runs=[],
        report={"code_metrics":{"toggle":{"dut":{"items":[{"file":"Probe.v","sha256":sha}]}}}}))
    app=FastAPI()
    app.include_router(routes(SimpleNamespace(state={"recent":[{"id":"project","path":str(root)}]}),TestRuns()))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url="http://test") as client:
        url="/api/chat/projects/project/coverage/source"
        response=await client.get(url,params={"instance":"Probe","metric":"toggle","file":"Probe.v"})
        assert response.status_code==200,response.text
        assert response.json()["file"]=="Probe.v"
        response=await client.get(url,params={"instance":"Probe","metric":"toggle","file":"../private"})
        assert response.status_code==404
        response=await client.get(url,params={"instance":"Probe","metric":"unknown"})
        assert response.status_code==422
