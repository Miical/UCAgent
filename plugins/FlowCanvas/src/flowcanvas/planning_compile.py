"""Prepare native planning inputs after the nested Picker stage accepts compilation."""
import shutil
from pathlib import Path
from ucagent.checkers.base import Checker, OrginFileMustExistChecker
from .picker_build import PickerPackageChecker

class DeferredDutFilesChecker(OrginFileMustExistChecker):
    """Run upstream DUT existence validation when its stage is entered."""
    def set_stage_manager(self, stage_manager):
        """Attach without requiring files that the preceding compilation will create."""
        return Checker.set_stage_manager(self, stage_manager)

    def on_init(self):
        """Use the original existence check before the native planning stage begins."""
        OrginFileMustExistChecker.set_stage_manager(self, self.stage_manager)
        return super().on_init()

class PlanningPickerChecker(PickerPackageChecker):
    """Install the accepted package in the native workflow's DUT directory."""
    def do_check(self, **kwargs):
        """Require Picker acceptance before exposing its package to planning stages."""
        ok, result = super().do_check(**kwargs)
        if not ok:
            return ok, result
        import json
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
            return True,{**result,"planning_dut":str(target)}
        except (OSError,ValueError) as exc:
            return False,{"error":str(exc),"next_action":"Check the accepted DUT package and retry Complete."}
