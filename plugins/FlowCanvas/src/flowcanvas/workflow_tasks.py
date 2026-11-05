"""Workflow task context attachments and conversation-bound MCP task execution."""
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel, Field
from .catalog import workflow_menu


class WorkflowInput(BaseModel):
    """Explicit local RTL input; the server chooses all task output paths."""
    workflow_id: str
    task_only: bool = False
    rtl_file: str = Field(default='', max_length=4096)
    dut: str = Field(default='RTL', pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=128)


def attachment(value):
    """Resolve only enabled catalog entries and existing RTL, never client-supplied configs."""
    item = next((row for row in workflow_menu() if row['id'] == value.workflow_id), None)
    if not item or not item['can_start']:
        raise HTTPException(400, 'Workflow is not available')
    if not value.rtl_file:
        return {**value.model_dump(), 'name': item['name']}
    source = Path(value.rtl_file).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {'.v', '.sv'}:
        raise HTTPException(400, 'Select an existing .v or .sv file')
    if source.stat().st_size > 8 * 1024 * 1024:
        raise HTTPException(400, 'RTL exceeds 8 MiB')
    return {**value.model_dump(), 'rtl_file': str(source), 'name': item['name']}
