"""Read source-bound branch, condition, FSM and assertion atoms from native URG text."""
import hashlib
import re


SECTION = r"^(?:Line|Toggle|Cond|FSM|Branch|Assert) Coverage for (?:Module|Instance) :[^\n]*"


def section(text, label):
    """Bound a metric section so other metric tables cannot contaminate its counts."""
    chunks = re.split("(" + SECTION + ")", text, flags=re.M)
    for index in range(1, len(chunks), 2):
        if chunks[index].startswith(label + " Coverage"):
            return chunks[index + 1]
    return ""


def read_metrics(header, detail, single, native, path, module, file, sha, raw):
    """Select instance observations, validate native denominators and preserve assertion outcomes."""
    result = {}
    for kind, label in [("cond", "Cond"), ("branch", "Branch"), ("fsm", "FSM"), ("assert", "Assert")]:
        body = section(detail, label) or (section(header, label) if single else "")
        if not body:
            continue
        items = []; states = []; expected = None
        def atom(name, line, hits, **extra):
            """Give each reported observation a deterministic instance-local identity."""
            key = hashlib.sha256((native + "\0" + kind + "\0" + str(len(items)) + "\0" + name).encode()).hexdigest()
            return dict(id=key, kind=kind, scope="dut", file=file, sha256=sha,
                        line=line, hits=hits, hierarchy=path, module=module, name=name, **extra)
        if kind in {"cond", "branch"}:
            title = "Conditions" if kind == "cond" else "Branches"
            expected = re.search(r"^" + title + r"\s+(\d+)\s+(\d+)", body, re.M)
            line = None; table = False; block = 0; source_line = None
            for text in body.splitlines():
                location = re.match(r"\s*LINE\s+(\d+)\s*$", text)
                listing = re.match(r"^\s*(\d+)\s{2,}\S", text)
                if location:
                    line = int(location[1]); block += 1; table = False
                elif kind == "branch" and listing and not table:
                    if source_line is None: source_line = int(listing[1])
                if re.match(r"\s*-\d+-.*Status", text):
                    table = True
                    if kind == "branch":
                        line = source_line; source_line = None; block += 1
                    continue
                status = re.match(r"\s*(.+?)\s+(Covered|Not Covered)\s*$", text)
                if table and status:
                    if line is None: raise ValueError("URG metric has no source location: " + native + ":" + kind)
                    items.append(atom(str(block) + ":" + " ".join(status[1].split()), line, int(status[2] == "Covered")))
                elif table and not text.strip():
                    table = False
        elif kind == "fsm":
            # URG explicitly excludes states from the FSM score; score transitions/sequences only.
            totals = re.findall(r"^(Transitions|Sequences)\s+(\d+)\s+(\d+)", body, re.M)
            expected = (sum(int(t[1]) for t in totals), sum(int(t[2]) for t in totals)) if totals else None
            machine = ""; category = None
            for text in body.splitlines():
                match = re.match(r"State, Transition and Sequence Details for FSM :: (.+)", text)
                if match: machine = match[1]
                match = re.match(r"(states|transitions|sequences)\s+Line No\.", text)
                if match: category = match[1]; continue
                match = re.match(r"(\S+)\s+(\d+)\s+(Covered|Not Covered)\s*$", text)
                if match and category:
                    entry = atom(machine + ":" + match[1], int(match[2]), int(match[3] == "Covered"), category=category)
                    if category == "states": states.append(entry)
                    else: items.append(entry)
        else:
            expected = re.search(r"^Assertions\s+(\d+)\s+(\d+)", body, re.M)
            table = False
            for text in body.splitlines():
                if re.match(r"Name\s+Attempts\s+Real Successes\s+Failures\s+Incomplete", text): table = True; continue
                match = re.match(r"(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", text)
                if table and match:
                    location = re.search(r"\b" + re.escape(match[1]) + r"\b", raw.decode(errors="replace"))
                    line = raw.decode(errors="replace")[:location.start()].count("\n") + 1 if location else None
                    items.append(atom(match[1], line, int(int(match[2]) > 0), attempts=int(match[2]),
                                      successes=int(match[3]), failures=int(match[4]), incomplete=int(match[5])))
                elif table and text.strip() and not text.startswith("-"):
                    table = False
        if expected is None:
            raise ValueError("URG metric summary missing: " + native + ":" + kind)
        total, covered = (int(expected[1]), int(expected[2])) if isinstance(expected, re.Match) else expected
        if len(items) != total or sum(a["hits"] > 0 for a in items) != covered:
            raise ValueError("URG metric details disagree with native totals: " + native + ":" + kind + f" (details {sum(a['hits'] > 0 for a in items)}/{len(items)}, summary {covered}/{total})")
        if items:
            result[kind] = dict(covered=covered, total=total, items=items)
            if kind == "fsm": result[kind]["states"] = states
    return result
