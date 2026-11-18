"""Prepare native planning inputs after the nested Picker stage accepts compilation."""
import json
import shutil
from pathlib import Path
from ucagent.checkers.base import Checker, OrginFileMustExistChecker
from ucagent.util.config import load_yaml_with_env_vars


def goal_planning_overrides(config, overrides):
    """Run the registered Picker workflow before the native planning stages."""
    from .plugin import get_plugin
    picker_config = next(workflow.config_file for workflow in get_plugin().workflows
                         if workflow.name == "picker-compile")
    picker = load_yaml_with_env_vars(picker_config)
    planning = load_yaml_with_env_vars(config)
    stages = picker["stage"]
    stages[0]["task"] = [picker["mission"]["prompt"]["system"], *stages[0].get("task", [])]
    stages[-1].setdefault("checker", []).append({
        "name": "planning_dut", "clss": "flowcanvas.planning_compile.PlanningDutHandoff",
    })
    return {**{f"template_overwrite.{key}": value
               for key, value in picker["template_overwrite"].items()
               if f"template_overwrite.{key}" not in overrides},
            "call_time_out": picker["call_time_out"],
            "stage": [{"name": "picker_compile", "desc": picker["mission"]["name"],
                       "task": [], "stage": stages}, *planning["stage"]]}

class DeferredDutFilesChecker(OrginFileMustExistChecker):
    """Run upstream DUT existence validation when its stage is entered."""
    def set_stage_manager(self, stage_manager):
        """Attach without requiring files that the preceding compilation will create."""
        return Checker.set_stage_manager(self, stage_manager)

    def on_init(self):
        """Use the original existence check before the native planning stage begins."""
        OrginFileMustExistChecker.set_stage_manager(self, self.stage_manager)
        return super().on_init()

class PlanningDutHandoff(Checker):
    """Install the accepted package in the native workflow's DUT directory."""

    def __init__(self, **kwargs):
        """Initialize the planning handoff gate."""
        super().__init__()

    def do_check(self, **kwargs):
        """Stage the package after the preceding Picker checkers have accepted it."""
        spec=json.loads((Path(self.workspace).parent/"request.json").read_text())
        source=Path(spec["dut_output"])
        target=Path(self.workspace)/spec["dut"]
        try:
            def copy_file(src,dst):
                """Preserve identical staged RTL, including its protected file mode."""
                if Path(dst).exists() and Path(src).read_bytes()==Path(dst).read_bytes():
                    return str(dst)
                return shutil.copy2(src,dst)
            shutil.copytree(source,target,dirs_exist_ok=True,copy_function=copy_file,
                            ignore=shutil.ignore_patterns("__pycache__"))
            if not (target/"__init__.py").is_file():
                raise ValueError("Compiled package is missing __init__.py")
            return True,{"planning_dut":str(target)}
        except (OSError,ValueError) as exc:
            return False,{"error":str(exc),"next_action":"Check the accepted DUT package and retry Complete."}
