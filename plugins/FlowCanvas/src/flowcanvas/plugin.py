"""Declare FlowCanvas as an opt-in UCAgent plugin."""
from pathlib import Path
from ucagent.plugins import Plugin, PluginWorkflow
from . import __version__


def get_plugin() -> Plugin:
    """Expose the web application and bundled workflow entries."""
    root = Path(__file__).resolve().parent
    return Plugin(name="flowcanvas", version=__version__,
                  description="Visual verification workspace", root=root,
                  assets=(root / "web",),
                  workflows=(PluginWorkflow(name="import-targets", config_file=root / "workflows" / "import-targets.yaml"),
                             PluginWorkflow(name="plan-goals", config_file=root / "workflows" / "plan-goals.yaml"),
                             PluginWorkflow(name="picker-compile", config_file=root / "workflows" / "picker-compile.yaml"),
                             PluginWorkflow(name="generate-testcase", config_file=root / "workflows" / "generate-testcase.yaml"),
                             PluginWorkflow(name="rtl-summary",
                                            config_file=root / "workflows" / "rtl-summary.yaml"),
                             PluginWorkflow(name="default",
                                            config_file=root / "workflows" / "default.yaml")))
