"""Gate planning on project panel records and publish their native checker index."""
import ast
import json
import re
from pathlib import Path

from ucagent.checkers.base import Checker
from .targets import LOCK, Targets


class PlanningTargetsChecker(Checker):
    """Validate panel identity and hierarchy before native stage acceptance."""

    def __init__(self, doc_file, leaf="CK", bindings=False, cov_file=None, complete_only=False, published_cov_file=None, **kwargs):
        """Select the required panel depth and optional implementation acceptance."""
        super().__init__()
        self.doc_file = doc_file
        self.leaf = leaf
        self.bindings = bindings
        self.cov_file = cov_file
        self.complete_only = complete_only
        self.published_cov_file = published_cov_file

    def do_check(self, **kwargs):
        """Reject absent cards or stale bindings; derive the native index from panel data."""
        try:
            with LOCK:
                request = json.loads((Path(self.workspace).parent / "request.json").read_text())
                store = Targets(request["project_root"])
                definition = store.read()
                mapping_path = Path(self.get_path(str(Path(self.doc_file).parent / "flowcanvas-target-map.json")))
                mapping = json.loads(mapping_path.read_text())
                if not isinstance(mapping, dict) or not mapping:
                    raise ValueError("Target map must be a nonempty object of full FG/FC/CK paths to panel IDs")
                targets = {target.id: target for target in definition.targets}
                if len(set(mapping.values())) != len(mapping):
                    raise ValueError("Target map must be one-to-one")
                if set(mapping.values()) != set(targets):
                    missing = sorted(set(targets) - set(mapping.values()))
                    unknown = sorted(set(mapping.values()) - set(targets))
                    raise ValueError(f"Map must cover every project card: unmapped={missing[:10]}, unknown={unknown[:10]}")
                prefixes = {"group": "FG", "point": "FC", "testpoint": "CK"}
                levels = {"FG": 1, "FC": 2, "CK": 3}
                paths = {}
                for key, identity in mapping.items():
                    target = targets[identity]
                    parts = key.split("/")
                    depth = levels[prefixes[target.kind]]
                    if len(parts) != depth or any(
                        not re.fullmatch(prefix + r"-[A-Za-z0-9_-]+", part)
                        for prefix, part in zip(("FG", "FC", "CK"), parts)
                    ):
                        raise ValueError(f"Invalid label path for {identity}: {key}")
                    if depth > 1 and mapping.get("/".join(parts[:-1])) != target.parent:
                        raise ValueError(f"Panel parent differs from map for {identity}: {key}")
                    if target.lifecycle != "ready" or not target.description.strip():
                        raise ValueError(f"Card {identity} needs finished creation and a description")
                    # Labels in prose would introduce records outside the authoritative panel tree.
                    if re.search(r"<(?:FG|FC|CK)-[^>]+>", target.name + target.description):
                        raise ValueError(f"Remove native label markup from card text: {identity}")
                    paths[identity] = key
                required_depth = levels[self.leaf]
                for target in targets.values():
                    depth = levels[prefixes[target.kind]]
                    if depth < required_depth and not any(child.parent == target.id for child in targets.values()):
                        raise ValueError(f"Card {target.id} has no child at required depth {self.leaf}")
                if not any(levels[prefixes[t.kind]] == required_depth for t in targets.values()):
                    raise ValueError(f"No {self.leaf} cards registered in the panel")
                if self.bindings and (not self.complete_only or kwargs.get("is_complete", False)):
                    status = store.status()
                    failures = []
                    for identity, target in targets.items():
                        if levels[prefixes[target.kind]] > required_depth:
                            continue
                        report = status["reports"][identity]
                        implementations = [b for b in target.bindings
                                           if b.role in {"definition", "coverage", "check"}
                                           and Path(b.file).suffix in {".py", ".sv", ".svh"}]
                        if report["status"] != "bound" or not implementations:
                            failures.append({"id": identity, "status": report["status"]})
                    if failures:
                        return False, {
                            "error_code": "PANEL_BINDINGS_REQUIRED",
                            "error": "Every accepted panel card needs current implementation source bindings",
                            "observed": failures[:20],
                            "next_action": "Publish implementation in the project; inspect and bind each original card. "
                                           "Read changed source and reconcile it before accept; do not bind RTL as implementation.",
                        }
                    if self.cov_file:
                        source = Path(self.get_path(self.cov_file))
                        published = store.file(self.published_cov_file or f"verification/coverage/{request['dut']}/{source.name}")
                        if ast.dump(ast.parse(source.read_text())) != ast.dump(ast.parse(published.read_text())):
                            raise ValueError("Published coverage implementation differs from the native checked source")
                        # An unrelated bound helper cannot stand in for the accepted coverage model.
                        for identity, target in targets.items():
                            if levels[prefixes[target.kind]] <= required_depth and not any(
                                store.file(b.file) == published and b.role in {"definition", "coverage", "check"}
                                for b in target.bindings
                            ):
                                raise ValueError(f"Card {identity} must bind its node in the published coverage model")
                lines = ["# FlowCanvas target index", "", "Generated from project panel records; do not edit.", ""]
                def emit(parent):
                    """Render one panel subtree in native FG/FC/CK order."""
                    for target in definition.targets:
                        if target.parent != parent:
                            continue
                        depth = levels[prefixes[target.kind]]
                        lines.extend(["#" * (depth + 1) + " " + target.name, "",
                                      "<" + paths[target.id].split("/")[-1] + ">", "",
                                      target.description, ""])
                        emit(target.id)
                emit(None)
                output = Path(self.get_path(self.doc_file))
                output.parent.mkdir(parents=True, exist_ok=True)
                content = "\n".join(lines)
                if not output.exists() or output.read_text() != content:
                    output.write_text(content)
                return True, {"panel_cards": len(targets), "revision": definition.revision,
                              "index": self.doc_file, "bindings_required": self.bindings}
        except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
            return False, {
                "error_code": "PANEL_TARGETS_INVALID",
                "error": str(exc),
                "artifact": self.doc_file,
                "next_action": "Use verification_targets guide/read to finish panel records and repair the "
                               "full-label-path to ID map. Do not edit the generated document.",
            }
