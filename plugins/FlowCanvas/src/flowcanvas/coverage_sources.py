"""Read fingerprint-verified build sources for coverage views and conversation context."""
import hashlib
import re
from pathlib import Path
from fastapi import HTTPException
from .coverage_runtime import state


def source_snapshot(project_root, instance, metric='line', file=None):
    """Resolve a report-owned source snapshot, rejecting paths or content outside its build."""
    data=state(project_root);report=data.get('report') or {}
    if metric == "line":
        row=next((n for n in report.get('instances',[]) if n['path']==instance),None)
        if not row:raise HTTPException(404,'Instance has no coverage source')
        files=row.get('files',[])
        source=next((entry for entry in files if not file or entry["path"]==file),None)
    else:
        if metric not in {"toggle","branch","expr","cond","fsm","assert"}:
            raise HTTPException(422,"Unknown coverage metric")
        items=report.get("code_metrics",{}).get(metric,{}).get("dut",{}).get("items",[])
        entry=next((item for item in items if (not item.get("hierarchy") or item["hierarchy"]==instance) and (not file or item["file"]==file)),None)
        source={"path":entry["file"],"sha256":entry["sha256"]} if entry else None
        row={"module":(entry or {}).get("module") or instance.split(".")[-1]}
    if not source:raise HTTPException(404,'Coverage report has no matching source mapping')
    expected=source.get('sha256') or report.get('rtl_sha256','')
    if not re.fullmatch('[0-9a-f]{64}',expected):raise HTTPException(422,'Source fingerprint is missing')
    project=Path(project_root).resolve()
    file=source['path']
    candidate=(project/file).resolve()
    if not candidate.is_relative_to(project):raise HTTPException(422,'Source path is outside the project')
    cache=project/'output/flowcanvas/coverage/sources'/(expected+Path(file).suffix)
    path=cache if cache.is_file() else candidate
    if not path.is_file():raise HTTPException(404,'Build source snapshot is unavailable')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise HTTPException(422,'Source differs from the build fingerprint')
    text=raw.decode()
    match=re.search(r'\bmodule\s+'+re.escape(row['module'])+r'\b[\s\S]*?\bendmodule\b',text)
    return dict(build_id=data.get('build_id'),sha256=expected,file=file,module=row['module'],content=text,
                line=text.count('\n',0,match.start())+1 if match else 1,
                end_line=text.count('\n',0,match.end())+1 if match else len(text.splitlines()))
