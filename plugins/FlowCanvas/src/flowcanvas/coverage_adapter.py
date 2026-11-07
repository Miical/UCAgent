"""Provider adapters for portable FlowCanvas coverage reports and exact merges."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .coverage_schema import validate_report
from .targets import Targets, atomic


def identity(root, inputs):
    """Hash explicitly selected build, RTL and coverage-definition inputs."""
    root = Path(root).resolve()
    files = json.loads(Path(inputs).read_text())
    if not isinstance(files, list) or not files or not all(isinstance(name,str) and not Path(name).is_absolute() for name in files):
        raise ValueError("Identity inputs must be a nonempty list of project files")
    digest = hashlib.sha256()
    for name in sorted(set(files)):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Identity input must be an existing project file")
        digest.update(name.encode() + b"\0")
        with path.open("rb") as source:
            while chunk := source.read(1024*1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def lcov_instances(root, filename, module):
    """Read executable-line hit counts and cache the exact project source snapshot."""
    root = Path(root).resolve()
    files = {}
    current = None
    for line in Path(filename).read_text().splitlines():
        if line.startswith("SF:"):
            path = (root / line[3:]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("LCOV source must resolve to an existing project file")
            current = str(path.relative_to(root))
            if current not in files:
                raw = path.read_bytes()
                sha = hashlib.sha256(raw).hexdigest()
                cache = root / ".flowcanvas/coverage/sources" / (sha + path.suffix)
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(raw)
                files[current] = dict(path=current, sha256=sha, line_hits={})
        elif line.startswith("DA:"):
            if current is None:
                raise ValueError("LCOV DA precedes its SF")
            values = line[3:].split(",")
            number, hits = int(values[0]), int(values[1])
            if number < 1 or hits < 0:
                raise ValueError("Invalid LCOV line observation")
            previous = files[current]["line_hits"]
            previous[str(number)] = previous.get(str(number), 0) + hits
        elif line == "end_of_record":
            current = None
    if not files or not any(f["line_hits"] for f in files.values()):
        raise ValueError("LCOV has no executable line observations")
    return [line_instance(module, list(files.values()))]


def line_instance(module, files):
    """Derive line totals from observed executable lines rather than percentages."""
    for file in files:
        file["covered_lines"] = sorted(int(k) for k,v in file["line_hits"].items() if v > 0)
        file["uncovered_lines"] = sorted(int(k) for k,v in file["line_hits"].items() if v == 0)
        file["partial_lines"] = []
    return dict(path=module, module=module, scope="design", unit="executable_lines",
                covered=sum(len(f["covered_lines"]) for f in files),
                total=sum(len(f["line_hits"]) for f in files), files=files)


def native_verilator_metrics(root, directory, module):
    """Separate native coverage kinds and DUT files from generated wrapper evidence."""
    import re
    root = Path(root).resolve()
    metadata = root / ".flowcanvas/project.json"
    project = json.loads(metadata.read_text()) if metadata.is_file() else {}
    sources = [(root / name).resolve() for name in project.get("rtl", {}).get("files", [])]
    hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in sources if path.is_file()}
    records = {}
    for database in sorted(Path(directory).rglob("*.dat")):
        if not database.resolve().is_relative_to(Path(directory).resolve()):
            raise ValueError("Coverage database escapes the run directory")
        lines = database.read_text().splitlines()
        if not lines or lines[0] != "# SystemC::Coverage-3":
            raise ValueError("Unsupported Verilator coverage database")
        for line in lines[1:]:
            if not line.strip():
                continue
            match = re.fullmatch(r"C '(.*)' ([0-9]+)", line)
            if not match:
                raise ValueError("Malformed Verilator coverage record")
            fields = dict(re.findall(r"\x01([^\x02]+)\x02([^\x01]*)", match[1]))
            kind = fields.get("page", "").split("/", 1)[0].removeprefix("v_")
            if kind not in {"line", "toggle", "branch", "expr", "fsm", "cond"}:
                continue
            file = (root / fields["f"]).resolve()
            if not file.is_relative_to(root) or not file.is_file():
                raise ValueError("Native coverage source must exist in the project")
            sha = hashlib.sha256(file.read_bytes()).hexdigest()
            cache = root / ".flowcanvas/coverage/sources" / (sha + file.suffix)
            if not cache.is_file():
                cache.parent.mkdir(parents=True,exist_ok=True)
                cache.write_bytes(file.read_bytes())
            scope = "dut" if (sha in hashes if hashes else file.stem == module) else "generated"
            key = hashlib.sha256(match[1].encode()).hexdigest()
            item = records.setdefault(key, dict(id=key,kind=kind,scope=scope,file=str(file.relative_to(root)),
                line=int(fields["l"]),sha256=sha,hits=0,hierarchy=fields.get("h",""),
                module=fields.get("page","").partition("/")[2]))
            item["hits"] += int(match[2])
            if kind == "line":
                item["lines"] = [item["line"]]
                if fields.get("S"):
                    for span in fields["S"].split(","):
                        lo, _, hi = span.partition("-")
                        item["lines"].extend(range(int(lo), int(hi or lo)+1))
    metrics = summarize_native(list(records.values()))
    line_files = {}
    for item in records.values():
        if item["kind"] != "line" or item["scope"] != "dut":
            continue
        file = root / item["file"]
        cache = root / ".flowcanvas/coverage/sources" / (item["sha256"] + file.suffix)
        cache.parent.mkdir(parents=True,exist_ok=True)
        cache.write_bytes(file.read_bytes())
        row = line_files.setdefault(item["file"],dict(path=item["file"],sha256=item["sha256"],line_hits={}))
        for line in set(item["lines"]):
            row["line_hits"][str(line)] = row["line_hits"].get(str(line),0) + item["hits"]
    return ([line_instance(module,list(line_files.values()))] if line_files else []), metrics


def summarize_native(items):
    """Retain atomic coverage kinds for precise merging and separate scope summaries."""
    result = {}
    for kind in sorted({item["kind"] for item in items}):
        result[kind] = {}
        for scope in ("dut","generated"):
            selected = [item for item in items if item["kind"]==kind and item["scope"]==scope]
            result[kind][scope] = dict(covered=sum(item["hits"]>0 for item in selected),
                                      total=len(selected),items=selected)
    return result


def toffee_report(root, report_file, build_id, mapping_file=None, lcov=None, module=None, dat_dir=None):
    """Adapt native Toffee group/bin counts plus optional LCOV without importing a DUT."""
    root = Path(root).resolve()
    report_path = Path(report_file)
    if report_path.is_dir():
        matches = list(report_path.rglob("toffee_report.json"))
        if len(matches) != 1:
            raise ValueError("Expected exactly one toffee_report.json in this run directory, found " + str(len(matches)))
        report_path = matches[0]
        if not report_path.resolve().is_relative_to(Path(report_file).resolve()):
            raise ValueError("Toffee report escapes the run report directory")
    raw = json.loads(report_path.read_text())
    coverages = raw.get("coverages", {})
    functional = coverages.get("functional")
    observations = {}
    if functional is not None:
        if not mapping_file:
            raise ValueError("Toffee functional coverage requires a label-path to target-ID map")
        mapping = json.loads(Path(mapping_file).read_text())
        store = Targets(root)
        status = store.status()
        targets = {row["id"]:row for row in status["targets"]}
        for group in functional["groups"]:
            for point in group["points"]:
                for bin in point["bins"]:
                    label = "/".join((group["name"], point["name"], bin["name"]))
                    leaf = targets.get(mapping.get(label))
                    if not leaf or leaf["kind"] != "testpoint":
                        raise ValueError("Unmapped functional coverage bin: " + label)
                    hits = bin["hints"]
                    if type(hits) is not int or hits < 0:
                        raise ValueError("Invalid Toffee bin hit count")
                    chain = [leaf, targets[leaf["parent"]], targets[targets[leaf["parent"]]["parent"]]]
                    for target in chain:
                        if target["lifecycle"] != "ready" or status["reports"][target["id"]]["status"] != "bound":
                            raise ValueError("Functional source bindings are not current: " + target["id"])
                        item = observations.setdefault(target["id"], dict(target_id=target["id"],
                            binding_fingerprints={b["id"]:b["fingerprint"] for b in target["bindings"]},
                            bins=[]))
                        item["bins"].append(dict(name=label, hits=hits, goal=1))
    line_info = coverages.get("line")
    if line_info and line_info.get("error") and not (lcov or dat_dir):
        raise ValueError("Toffee line collection failed: " + str(line_info["error"]))
    if line_info and not (lcov or dat_dir):
        raise ValueError("Provide LCOV converted from this run's native line database")
    metrics = {}
    if dat_dir:
        instances, metrics = native_verilator_metrics(root,dat_dir,module)
    else:
        instances = lcov_instances(root, lcov, module) if lcov else []
    report = dict(schema_version=1, build_id=build_id,
                  run_id=hashlib.sha256(str(report_path.resolve()).encode()).hexdigest(),
                  provider="toffee", instances=instances,
                  line_basis="verilator-native-line" if dat_dir else "lcov-unspecified",
                  code_metrics=metrics,
                  functional=dict(groups=[], observations=list(observations.values())))
    if dat_dir and not instances:
        report["line_reason"] = "no_instrumented_dut_lines"
    if line_info and line_info.get("error"):
        report["warnings"] = ["Native Toffee line report failed; using independent " +
                              ("typed Verilator data: " if dat_dir else "LCOV: ") + str(line_info["error"])]
    return validate_report(report)


def merge_reports(paths):
    """Merge observed line and bin counts, rejecting incompatible source snapshots."""
    reports = [validate_report(json.loads(Path(path).read_text())) for path in dict.fromkeys(paths)]
    if not reports or len({r["build_id"] for r in reports}) != 1:
        raise ValueError("Coverage merge requires reports from one build")
    if len(reports) == 1:
        return reports[0]
    if len({r.get("line_basis","legacy") for r in reports}) != 1:
        raise ValueError("Coverage measurement changed; re-extract this batch from original artifacts")
    if any(r["functional"]["groups"] for r in reports):
        raise ValueError("Legacy functional groups require their native database merge adapter")
    instances, targets, atoms = {}, {}, {}
    seen_runs = set()
    for report in reports:
        run_id = report.get("run_id")
        if run_id and run_id in seen_runs:
            continue
        if run_id:
            seen_runs.add(run_id)
        for scopes in report.get("code_metrics",{}).values():
            for summary in scopes.values():
                for item in summary["items"]:
                    old = atoms.get(item["id"])
                    if old and (old["sha256"] != item["sha256"] or old["scope"] != item["scope"]):
                        raise ValueError("Incompatible native coverage identities")
                    atoms[item["id"]] = dict(item,hits=item["hits"]+(old["hits"] if old else 0))
        for source in report["instances"]:
            if source.get("unit") != "executable_lines" or not source.get("files"):
                raise ValueError("Exact line merging requires line hit observations, not aggregate percentages")
            instance = instances.setdefault(source["path"], dict(module=source["module"], files={}))
            if instance["module"] != source["module"]:
                raise ValueError("Incompatible coverage module identities")
            for file in source["files"]:
                if "line_hits" not in file or not file.get("sha256"):
                    raise ValueError("Line merging requires counters and source fingerprints")
                old = instance["files"].get(file["path"])
                if old:
                    if old["sha256"] != file["sha256"] or set(old["line_hits"]) != set(file["line_hits"]):
                        raise ValueError("Incompatible executable line identities")
                    old["line_hits"] = {k:v+file["line_hits"][k] for k,v in old["line_hits"].items()}
                else:
                    instance["files"][file["path"]] = json.loads(json.dumps(file))
        for source in report["functional"]["observations"]:
            old = targets.setdefault(source["target_id"], dict(target_id=source["target_id"],
                binding_fingerprints=source["binding_fingerprints"], bins={}))
            if old["binding_fingerprints"] != source["binding_fingerprints"]:
                raise ValueError("Incompatible functional source bindings")
            for bin in source["bins"]:
                previous = old["bins"].get(bin["name"])
                if previous and previous["goal"] != bin["goal"]:
                    raise ValueError("Incompatible functional bin goals")
                old["bins"][bin["name"]] = dict(bin, hits=bin["hits"]+(previous["hits"] if previous else 0))
    return validate_report(dict(schema_version=1,build_id=reports[0]["build_id"],
        line_basis=reports[0].get("line_basis","legacy"),code_metrics=summarize_native(list(atoms.values())),
        line_reason="no_instrumented_dut_lines" if not instances and atoms else "",
        instances=[line_instance(row["module"],list(row["files"].values())) for row in instances.values()],
        functional=dict(groups=[],observations=[dict(row,bins=list(row["bins"].values())) for row in targets.values()])))



def convert_verilator_run(run_directory, output, project, executable=None):
    """Convert only this run's native databases; never reuse an old coverage file."""
    directory = Path(run_directory).resolve()
    databases = sorted(directory.rglob("*.dat"))
    if not databases:
        raise ValueError("No Verilator .dat coverage in this run; check SetCoverage and coverage-enabled build")
    for path in databases:
        if not path.resolve().is_relative_to(directory):
            raise ValueError("Coverage database escapes the current run directory")
    tool = executable or shutil.which("verilator_coverage")
    if not tool:
        raise ValueError("verilator_coverage is not available on PATH")
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(tool), "--write-info", str(target), *map(str, databases)],
                   cwd=str(project), check=True, timeout=120, capture_output=True)
    if not target.is_file() or not target.stat().st_size:
        raise ValueError("Verilator conversion produced no LCOV data")
    return target


def main():
    """Expose extraction and merging commands for project coverage configuration."""
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["extract", "merge", "identity"])
    parser.add_argument("--adapter", choices=["toffee", "normalized"], default="toffee")
    parser.add_argument("--project")
    parser.add_argument("--report")
    parser.add_argument("--mapping")
    parser.add_argument("--lcov")
    parser.add_argument("--dat-dir", help="Convert this run's Verilator .dat files to LCOV first")
    parser.add_argument("--module")
    parser.add_argument("--build-id")
    parser.add_argument("--inputs-manifest")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.operation == "identity":
        print(identity(args.project, args.inputs_manifest))
        return
    if args.operation == "merge":
        result = merge_reports(json.loads(Path(args.inputs_manifest).read_text()))
    elif args.adapter == "normalized":
        result = validate_report(json.loads(Path(args.report).read_text()), args.build_id)
    else:
        if args.dat_dir:
            if args.lcov or not args.output or not args.project:
                parser.error("dat-dir requires project/output and cannot be combined with lcov")
            args.lcov = str(convert_verilator_run(args.dat_dir, Path(args.output).with_suffix(".info"), args.project))
        if not args.project or not args.report or not args.build_id or args.lcov and not args.module:
            parser.error("Toffee requires project/report/build-id; LCOV also requires module")
        result = toffee_report(args.project,args.report,args.build_id,args.mapping,args.lcov,args.module,args.dat_dir)
    if not args.output:
        parser.error("output is required")
    atomic(Path(args.output), result)


if __name__ == "__main__":
    main()
