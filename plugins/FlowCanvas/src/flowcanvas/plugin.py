"""Declare FlowCanvas as an opt-in UCAgent plugin."""
from pathlib import Path
from ucagent.plugins import Plugin
from . import __version__


def get_plugin() -> Plugin:
    """Expose the application assets; verification workflows will be added separately."""
    root = Path(__file__).resolve().parent
    return Plugin(name="flowcanvas", version=__version__,
                  description="Visual verification workspace", root=root,
                  assets=(root / "web",))
