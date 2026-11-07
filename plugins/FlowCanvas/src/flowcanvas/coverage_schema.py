"""Validate the shared coverage publication contract independently of providers."""
from copy import deepcopy


def validate_report(value, build_id=None):
    """Normalize legacy reports and reject invalid counts before publication."""
    report = deepcopy(value)
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("Unsupported coverage schema_version")
    if not isinstance(report.get("build_id"), str) or not report["build_id"]:
        raise ValueError("Coverage build_id is required")
    if build_id is not None and report["build_id"] != build_id:
        raise ValueError("Coverage build_id differs from the executed build")
    def count(value):
        """Require genuine nonnegative integer observations."""
        if type(value) is not int or value < 0:
            raise ValueError("Coverage counts must be nonnegative integers")
        return value
    def totals(row):
        """Validate numerator and denominator without inventing percentages."""
        if count(row["covered"]) > count(row["total"]):
            raise ValueError("Coverage count exceeds its total")
    instances = report.setdefault("instances", [])
    functional = report.setdefault("functional", {})
    groups = functional.setdefault("groups", [])
    observations = functional.setdefault("observations", [])
    if not isinstance(instances, list) or not isinstance(groups, list) or not isinstance(observations, list):
        raise ValueError("Coverage collections must be lists")
    seen = set()
    for row in instances:
        totals(row)
        if not isinstance(row.get("path"), str) or not row["path"] or row["path"] in seen:
            raise ValueError("Coverage instance paths must be unique")
        seen.add(row["path"])
        for file in row.get("files", []):
            if not isinstance(file.get("path"), str) or not file["path"]:
                raise ValueError("Coverage source path is required")
            hits = file.get("line_hits", {})
            for line, value in hits.items():
                if not str(line).isdigit() or int(line) < 1:
                    raise ValueError("Invalid coverage source line")
                count(value)
    seen = set()
    for group in groups:
        key = (group["file"], group["type"])
        if key in seen:
            raise ValueError("A coverage definition cannot represent multiple instances")
        seen.add(key)
        for point in group["points"]:
            totals(point)
            for bin in point.get("bins", []):
                count(bin["hits"])
                if count(bin["goal"]) == 0:
                    raise ValueError("Coverage bin goals must be positive")
    seen = set()
    for observation in observations:
        identity = observation["target_id"]
        if identity in seen:
            raise ValueError("Duplicate target coverage observation")
        seen.add(identity)
        if not observation.get("binding_fingerprints"):
            raise ValueError("Target observations require source binding fingerprints")
        bins = observation.get("bins")
        if not isinstance(bins, list) or not bins:
            raise ValueError("Target observations require actual bins")
        names = set()
        for bin in bins:
            if bin["name"] in names:
                raise ValueError("Duplicate target coverage bin")
            names.add(bin["name"])
            count(bin["hits"])
            if count(bin["goal"]) == 0:
                raise ValueError("Coverage bin goals must be positive")
    for kind, scopes in report.get("code_metrics", {}).items():
        for scope, summary in scopes.items():
            totals(summary)
            items = summary["items"]
            if summary["total"] != len(items) or summary["covered"] != sum(item["hits"] > 0 for item in items):
                raise ValueError("Native coverage summary differs from its atoms")
            ids = set()
            for item in items:
                count(item["hits"])
                if item["kind"] != kind or item["scope"] != scope or item["id"] in ids:
                    raise ValueError("Invalid native metric identity")
                ids.add(item["id"])
    report.setdefault("line_status", "available" if instances else "unavailable")
    functional.setdefault("status", "available" if groups or observations else "unavailable")
    for status in (report["line_status"], functional["status"]):
        if status not in {"available", "unavailable"}:
            raise ValueError("Invalid coverage availability status")
    if (instances and report["line_status"] != "available" or
            (groups or observations) and functional["status"] != "available"):
        raise ValueError("Unavailable coverage cannot carry observations")
    if report["line_status"] == "available" and not instances:
        raise ValueError("Available line coverage needs observations")
    if functional["status"] == "available" and not (groups or observations):
        raise ValueError("Available functional coverage needs observations")
    return report
