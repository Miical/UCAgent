"""Load the curated workflow menu without constructing an agent or executing a workflow."""
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .plugin import get_plugin


class CatalogEntry(BaseModel):
    """Presentation metadata referring to an independently registered workflow."""
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9-]*$')
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    workflow: str = Field(pattern=r'^[a-zA-Z0-9_-]+:[a-zA-Z0-9_-]+$')
    enabled: bool = True


class Catalog(BaseModel):
    """Ordered menu entries with explicit visibility controls."""
    model_config = ConfigDict(extra='forbid')
    workflows: list[CatalogEntry]


def workflow_menu():
    """Validate menu entries and expose registration status, never implying a runnable UI."""
    data = Catalog.model_validate(yaml.safe_load(
        Path(__file__).with_name('workflow_catalog.yaml').read_text(encoding='utf-8')))
    identities = [item.id for item in data.workflows]
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicate workflow menu id')
    plugin = get_plugin()
    registered = {plugin.name + ':' + workflow.name: workflow.config_file
                  for workflow in plugin.workflows}
    result = []
    for item in data.workflows:
        if not item.enabled:
            continue
        path = registered.get(item.workflow)
        exists = bool(path and path.is_file())
        result.append({**item.model_dump(), 'registered': exists, 'can_start': False,
                       'status': 'launch_not_connected' if exists else 'not_registered'})
    return result
