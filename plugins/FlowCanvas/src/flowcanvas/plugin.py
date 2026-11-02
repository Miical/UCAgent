"""Declare FlowCanvas as an opt-in UCAgent plugin."""
from pathlib import Path
from ucagent.plugins import Plugin, PluginWorkflow
from . import __version__


def get_plugin() -> Plugin:
    """Expose the web application and bundled minimal RTL summary workflow."""
    root = Path(__file__).resolve().parent
    return Plugin(name="flowcanvas", version=__version__,
                  description="Visual verification workspace", root=root,
                  assets=(root / "web",),
                  workflows=(PluginWorkflow(name="rtl-summary",
                                            config_file=root / "workflows" / "rtl-summary.yaml"),))
