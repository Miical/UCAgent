"""Read completed, build-scoped Picker/Toffee artifact directories."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from .coverage_adapter import identity, merge_observations, toffee_report
from .targets import atomic


DATABASE = Path("output/flowcanvas/coverage/database/toffee")
BUILD_INPUTS = Path("verification/coverage/build-inputs.json")
MAPPING = Path("verification/coverage/flowcanvas-map.json")
CLEARED_RUNS = ".cleared-runs.json"


def enabled(root):
    """Prefer a configured VCS project when both native directories exist."""
    root = Path(root)
    database = root / DATABASE
    if not database.is_dir() or not database.resolve().is_relative_to(root.resolve()):
        return False
    config = database.parents[1] / "config.json"
    return not config.is_file() or json.loads(config.read_text()).get("provider") != "vcs"


def file_stamp(path):
    stat = path.stat()
    return path, stat.st_mtime_ns, stat.st_size


def completed_runs(database):
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
def build_identity(root, stamps):
    return identity(root, root / BUILD_INPUTS)


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
            report = toffee_report(root, toffee, build_id, root / MAPPING,
                                   module=module, dat_dir=run_dir / "raw")
            report["run_id"] = run_id
            if not report["instances"] and not report["functional"]["observations"] and not any(
                metric.get("dut", {}).get("total") for metric in report["code_metrics"].values()
            ):
                raise ValueError("没有 DUT 覆盖率数据")
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
        inputs = root / BUILD_INPUTS
        files = [(root / name).resolve() for name in json.loads(inputs.read_text())]
        if not all(file.is_relative_to(root) for file in [inputs.resolve(), *files]):
            raise ValueError("覆盖率构建输入必须在项目内")
        build_id = build_identity(root, tuple(map(file_stamp, [inputs, *files])))
        runs = []
        for folder in folders:
            manifest = folder / "run.json"
            files = [manifest, *sorted((folder / "toffee").rglob("toffee_report.json")),
                     *sorted((folder / "raw").rglob("*.dat"))]
            if not all(file.resolve().is_relative_to(folder) for file in files):
                raise ValueError("覆盖率产物必须在所属运行目录内")
            runs.append((folder, tuple(file_stamp(file) for file in files)))
        report, accepted, error = read_runs(root, module, build_id, tuple(runs))
        revision = hashlib.sha256(repr((module, build_id, runs)).encode()).hexdigest()
        result.update(build_id=build_id, report=report, runs=accepted, revision=revision)
        if error:
            result["ingestion_error"] = error
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result.update(ingestion_error=str(exc), revision=str(exc))
    return result
