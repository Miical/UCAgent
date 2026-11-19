"""Publish and read build-scoped Picker/Toffee run artifacts."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from .coverage_adapter import identity, merge_observations, toffee_report
from .targets import atomic


DATABASE = Path("output/flowcanvas/coverage/database/toffee")
BUILD_INPUTS = Path("verification/coverage/build-inputs.json")
MAPPING = Path("verification/coverage/flowcanvas-map.json")
CLEARED_RUNS = ".cleared-runs.json"


def enabled(root):
    """Honor the configured provider and discover existing native run directories."""
    root = Path(root).resolve()
    database = root / DATABASE
    if not database.resolve().is_relative_to(root):
        return False
    definition = root / ".flowcanvas/testcases.json"
    if definition.is_file():
        config = json.loads(definition.read_text()).get("coverage") or {}
        if config.get("provider") in {"toffee", "vcs"}:
            return config["provider"] == "toffee"
    if not database.is_dir():
        return False
    config = database.parents[1] / "config.json"
    return not config.is_file() or json.loads(config.read_text()).get("provider") != "vcs"


def file_stamp(path):
    stat = path.stat()
    return path, stat.st_mtime_ns, stat.st_size


def completed_runs(database):
    if not database.is_dir():
        return []
    return [folder for folder in sorted(database.iterdir())
            if not folder.name.startswith(".") and not folder.is_symlink()
            and folder.is_dir() and (folder / "run.json").is_file()]


def cleared_runs(database):
    marker = database / CLEARED_RUNS
    return set(json.loads(marker.read_text())) if marker.is_file() else set()


def reset(root):
    """Exclude completed runs from accumulation while keeping their original files."""
    database = Path(root).resolve() / DATABASE
    excluded = cleared_runs(database) | {folder.name for folder in completed_runs(database)}
    atomic(database / CLEARED_RUNS, sorted(excluded))
    return public(root)


@lru_cache(maxsize=8)
def _build_identity(root, stamps):
    return identity(root, root / BUILD_INPUTS)


def build_identity(root):
    root = Path(root).resolve()
    inputs = (root / BUILD_INPUTS).resolve()
    if not inputs.is_relative_to(root):
        raise ValueError("覆盖率构建清单必须在项目内")
    files = [(root / name).resolve() for name in json.loads(inputs.read_text())]
    if not all(file.is_relative_to(root) for file in files):
        raise ValueError("覆盖率构建输入必须在项目内")
    return _build_identity(root, tuple(map(file_stamp, [inputs, *files])))


def artifact_files(run_dir):
    reports = sorted((run_dir / "toffee").rglob("toffee_report.json"))
    databases = sorted((run_dir / "raw").rglob("*.dat"))
    if not all(file.resolve().is_relative_to(run_dir) for file in [*reports, *databases]):
        raise ValueError("覆盖率产物必须在所属运行目录内")
    return reports, databases


def read_report(root, run_dir, module, build_id, report_path):
    report = toffee_report(root, report_path, build_id, root / MAPPING,
                           module=module, dat_dir=run_dir / "raw")
    if not report["instances"] and not report["functional"]["observations"] and not any(
        metric.get("dut", {}).get("total") for metric in report["code_metrics"].values()
    ):
        raise ValueError("没有 DUT 覆盖率数据")
    return report


def run_inputs(root, run_dir):
    """Resolve the native files and DUT for a managed run."""
    reports, databases = artifact_files(run_dir)
    if len(reports) != 1 or not databases:
        raise ValueError("本次运行必须输出一个 Toffee 报告以及 Verilator 覆盖率数据库")
    module = json.loads((root / ".flowcanvas/project.json").read_text())["rtl"]["top"]
    return reports[0], databases, module


def smoke_report(root, run_dir, expected_build):
    """Validate the native inputs in place without publishing them to the panel."""
    root, run_dir = Path(root).resolve(), Path(run_dir).resolve()
    if not run_dir.is_relative_to(root):
        raise ValueError("覆盖率产物必须位于项目内")
    report_path, _, module = run_inputs(root, run_dir)
    report = read_report(root, run_dir, module, expected_build, report_path)
    if build_identity(root) != expected_build:
        raise ValueError("运行期间覆盖率构建已变化")
    return report


def publish_run(root, run_dir, expected_build):
    """Publish a completed run atomically, retaining its original artifacts."""
    root, run_dir = Path(root).resolve(), Path(run_dir).resolve()
    database = (root / DATABASE).resolve()
    if not database.is_relative_to(root) or not run_dir.is_relative_to(root):
        raise ValueError("覆盖率产物必须位于项目内")
    if build_identity(root) != expected_build:
        raise ValueError("运行期间覆盖率构建已变化")
    report_path, databases, module = run_inputs(root, run_dir)
    destination = database / run_dir.name
    receipt = destination / "run.json"
    if destination.is_symlink() or receipt.is_symlink():
        raise ValueError("覆盖率发布目录和清单不能使用符号链接")
    if receipt.is_file():
        if json.loads(receipt.read_text()).get("build_id") == expected_build:
            return destination
        raise ValueError("同一运行已发布到另一个构建")
    database.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".publish-", dir=database) as temporary:
        staged = Path(temporary) / run_dir.name
        for source in [report_path, *databases]:
            target = staged / source.relative_to(run_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        report = read_report(root, staged, module, expected_build, staged / report_path.relative_to(run_dir))
        if build_identity(root) != expected_build:
            raise ValueError("发布期间覆盖率构建已变化")
        from .coverage_links import save_hits
        save_hits(run_dir, report, root)
        atomic(staged / "run.json", {"build_id": expected_build})
        staged.rename(destination)
    return destination


@lru_cache(maxsize=8)
def read_runs(root, module, build_id, runs):
    """Cache the derived snapshot until the run artifacts or build change."""
    reports, accepted, errors = [], [], []
    for run_dir, files in runs:
        run_id = run_dir.name
        try:
            metadata = json.loads((run_dir / "run.json").read_text())
            if metadata.get("build_id") != build_id:
                raise ValueError("运行产物属于其他构建")
            toffee = run_dir / "toffee" if any(path.name == "toffee_report.json" for path, *_ in files) else None
            report = read_report(root, run_dir, module, build_id, toffee)
            report["run_id"] = run_id
            reports.append(report)
            accepted.append({"id": run_id})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append(f"{run_id}: {exc}")
    try:
        merged = merge_observations(reports) if reports else None
    except ValueError as exc:
        return None, [], str(exc)
    return merged, accepted, errors[0] if errors else ""


def public(root):
    """Load published run directories without changing their logs or native artifacts."""
    root = Path(root).resolve()
    database = root / DATABASE
    result = dict(provider="toffee", native_artifacts=True, runs=[], report=None, build_id=None, revision="")
    try:
        excluded = cleared_runs(database)
        folders = [folder for folder in completed_runs(database) if folder.name not in excluded]
        result["can_reset"] = bool(folders)
        module = json.loads((root / ".flowcanvas/project.json").read_text())["rtl"]["top"]
        build_id = build_identity(root)
        runs = []
        for folder in folders:
            manifest = folder / "run.json"
            reports, databases = artifact_files(folder)
            if not manifest.resolve().is_relative_to(folder):
                raise ValueError("覆盖率产物必须在所属运行目录内")
            runs.append((folder, tuple(map(file_stamp, [manifest, *reports, *databases]))))
        report, accepted, error = read_runs(root, module, build_id, tuple(runs))
        revision = hashlib.sha256(repr((module, build_id, runs)).encode()).hexdigest()
        result.update(build_id=build_id, report=report, runs=accepted, revision=revision)
        if error:
            result["ingestion_error"] = error
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result.update(ingestion_error=str(exc), revision=str(exc))
    return result
