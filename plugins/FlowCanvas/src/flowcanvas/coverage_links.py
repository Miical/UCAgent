"""Connect planned and observed coverage to stable FlowCanvas card IDs."""
import ast
import json
from pathlib import Path
import re
from textwrap import dedent

from .line_bindings import fingerprint, read_source
from .targets import Targets, atomic
from .testcases import Cases

SNAPSHOT = "functional-hits.json"
FORMAL_MODES = {None, "test"}


def lineage(cards, identity):
    while identity in cards:
        card = cards[identity]
        yield card
        identity = card.parent


def target_row(card):
    return dict(id=card.id, name=card.name, kind=card.kind, parent=card.parent)


def save_hits(run_dir, report, root):
    """Store positive observations alongside the run, independent of accumulation."""
    observations = report.get("functional", {}).get("observations", [])
    cards = {card.id: card for card in Targets(root).read().targets}
    hits, partial = {}, False
    for row in observations:
        bins = row.get("bins", [])
        if not bins and row.get("total", 0):
            partial = True
        if not any(item["hits"] > 0 for item in bins) and not (not bins and row.get("covered", 0)):
            continue
        for card in lineage(cards, row["target_id"]):
            fingerprints = {binding.id: binding.fingerprint for binding in card.bindings}
            if card.id == row["target_id"] and fingerprints != row["binding_fingerprints"]:
                break
            hits[card.id] = {**target_row(card), "fingerprints": fingerprints}
    status = "partial" if partial else "available" if observations else "unavailable"
    atomic(Path(run_dir)/SNAPSHOT, {"status": status, "targets": list(hits.values())})


def snapshot(root, run):
    folder = (root/"output/flowcanvas/test-runs").resolve()
    identity = run["id"]
    if not re.fullmatch(r"[a-f0-9]{32}", identity):
        return {"status": "unavailable", "targets": []}
    directory = folder/identity
    path = (directory/SNAPSHOT).resolve()
    if not path.is_relative_to(directory) or not folder.is_relative_to(root):
        return {"status": "unavailable", "targets": []}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        status = "pending" if run.get("status") in {"starting", "running"} else "unavailable"
        return {"status": status, "targets": []}


def literal_marks(content, name):
    """Read literal Toffee declarations from the bound test without importing the fixture."""
    tree = ast.parse(dedent(content))
    entry = next((node for node in tree.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name), None)
    if entry is None:
        return
    for call in ast.walk(entry):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != "mark_function":
            continue
        receiver = call.func.value
        if (len(call.args) != 3 or not isinstance(receiver, ast.Subscript) or
                not isinstance(receiver.value, ast.Attribute) or receiver.value.attr != "fc_cover" or
                not isinstance(call.args[1], ast.Name) or call.args[1].id != name):
            continue
        try:
            group = ast.literal_eval(receiver.slice)
            point = ast.literal_eval(call.args[0])
            checks = ast.literal_eval(call.args[2])
        except (ValueError, SyntaxError, TypeError):
            continue
        if (isinstance(group, str) and isinstance(point, str) and isinstance(checks, (list, tuple))):
            yield from (f"{group}/{point}/{check}" for check in checks if isinstance(check, str))


def planned_targets(root, cases, case, target_status, case_reports, source_lines):
    """Combine current literal Toffee plans and explicit source-reviewed registrations."""
    from .native_toffee import MAPPING

    targets = {card["id"]:card for card in target_status["targets"]}
    reports = target_status["reports"]
    direct = {identity for identity, card in targets.items()
              if reports[identity]["explanation_status"] == "available" and card.get("explanation")
              and any(row["case_id"] == case.id for row in card["explanation"].get("related_cases", []))}
    mapping_path = (root/MAPPING).resolve()
    if mapping_path.is_relative_to(root) and mapping_path.is_file():
        try:
            mapping = json.loads(mapping_path.read_text())
            for binding in case.bindings:
                if binding.role != "definition" or not binding.file.endswith(".py"):
                    continue
                source = next((row for row in case_reports[case.id]["bindings"] if row["id"] == binding.id), None)
                if source and source["status"] == "bound":
                    if binding.file not in source_lines:
                        source_lines[binding.file] = read_source(cases.file(binding.file)).splitlines()
                    content = "\n".join(source_lines[binding.file][source["line"]-1:source["end_line"]])
                    if fingerprint(content) != binding.fingerprint:
                        continue
                    for tag in literal_marks(content, case.name.replace("::", ".").split(".")[-1]):
                        identity = mapping.get(tag)
                        if identity in targets and targets[identity]["kind"] == "testpoint":
                            direct.add(identity)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError):
            pass
    planned = {}
    for identity in direct:
        while identity in targets:
            card = targets[identity]
            planned[identity] = {key:card[key] for key in ("id", "name", "kind", "parent")}
            identity = card["parent"]
    return list(planned.values())


def batch_snapshots(root, runs):
    runs = list(runs)
    if not runs:
        return
    from .coverage_runtime import public
    batch = public(root)
    accepted = {record["id"] for record in batch.get("runs", [])}
    for run in runs:
        if run.get("mode") not in FORMAL_MODES or run["id"] not in accepted or run.get("build_id") != batch.get("build_id"):
            continue
        yield run, snapshot(root, run)


def is_current(hit, card, report):
    return (card is not None and card["parent"] == hit["parent"] and report["status"] == "bound" and
            {binding["id"]:binding["fingerprint"] for binding in card["bindings"]} == hit.get("fingerprints"))


def case_links(root, case_id, runs):
    root = Path(root).resolve()
    cases = Cases(root)
    case = next((card for card in cases.read().targets if card.id == case_id), None)
    if not case:
        raise LookupError("测试用例不存在")
    target_status = Targets(root).status(include_coverage=False)
    planned = planned_targets(root, cases, case, target_status, cases.status()["reports"], {})
    valid = {row["id"]:row for row in target_status["targets"]}
    actual, statuses = {}, []
    for run, result in batch_snapshots(root, (run for run in runs if run.get("case_id") == case_id)):
        statuses.append(result["status"])
        for hit in result["targets"]:
            current = valid.get(hit["id"])
            if current and is_current(hit, current, target_status["reports"][hit["id"]]):
                actual[hit["id"]] = {key:current[key] for key in ("id", "name", "kind", "parent")}
    status = ("unavailable" if not statuses or all(s == "unavailable" for s in statuses)
              else "available" if all(s == "available" for s in statuses) else "partial")
    return {"planned": planned, "actual": list(actual.values()), "status": status}



def case_target_filters(root, runs):
    """Project both target association types in one pass for the case list."""
    root = Path(root).resolve()
    cases = Cases(root)
    case_status = cases.status()
    target_status = Targets(root).status(include_coverage=False)
    targets = {card["id"]: card for card in target_status["targets"] if card["lifecycle"] != "draft"}
    source_lines = {}
    planned = {}
    for row in case_status["targets"]:
        if row["lifecycle"] == "draft":
            continue
        case = cases.target_model.model_validate(row)
        links = planned_targets(root, cases, case, target_status, case_status["reports"], source_lines)
        planned[case.id] = [hit["id"] for hit in links if hit["id"] in targets]
    actual = {}
    for run, result in batch_snapshots(root, (run for run in runs if run.get("case_id") in planned)):
        for hit in result["targets"]:
            card = targets.get(hit["id"])
            if card and is_current(hit, card, target_status["reports"][card["id"]]):
                ids = actual.setdefault(run["case_id"], set())
                while card and card["id"] not in ids:
                    ids.add(card["id"])
                    card = targets.get(card["parent"])
    return {
        "targets": [{key: card[key] for key in ("id", "name", "parent", "kind")} for card in targets.values()],
        "planned": planned,
        "actual": {case_id: sorted(ids) for case_id, ids in actual.items()},
    }

def run_links(root, run_id, runs):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise LookupError("运行不存在")
    run = next((row for row in runs if row["id"] == run_id), None)
    if not run:
        raise LookupError("运行不存在")
    result = snapshot(Path(root).resolve(), run)
    ids = {card.id for card in Targets(root).read().targets}
    return {**result, "case_id": run["case_id"], "targets": [
        {**{key:row[key] for key in ("id", "name", "kind", "parent")},
         "available": row["id"] in ids} for row in result["targets"]
    ]}


def target_links(root, target_id, runs):
    root = Path(root).resolve()
    targets = Targets(root).status(include_coverage=False)
    if target_id not in targets["reports"]:
        raise LookupError("验证目标不存在")
    if targets["reports"][target_id]["status"] != "bound":
        return []
    target = next(row for row in targets["targets"] if row["id"] == target_id)
    cards = {card.id:card for card in Cases(root).read().targets if card.lifecycle == "ready"}
    grouped = {}
    for run, result in batch_snapshots(root, runs):
        case = cards.get(run.get("case_id"))
        if case and any(row["id"] == target_id and
                        is_current(row, target, targets["reports"][target_id])
                        for row in result["targets"]):
            item = grouped.setdefault(case.id, {"case_id": case.id, "title": case.title or case.name, "runs": []})
            item["runs"].append({"run_id": run["id"], "started": run["started"]})
    return list(grouped.values())
