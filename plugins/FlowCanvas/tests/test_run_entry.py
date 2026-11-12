"""Managed Python entries remain valid when runner commands change directory."""
import asyncio
import shlex
import sys
import pytest
from flowcanvas.testcases import Cases, RunTemplate
from target_test_support import reserve_case, bind_case
from flowcanvas.test_runner import TestRuns as Runs


@pytest.mark.asyncio
async def test_python_entry_survives_cd_and_project_spaces(tmp_path):
    """Run a harmless pytest assertion from a different cwd using the bound entry."""
    root=tmp_path/"project with spaces"
    source=root/"verification/test_entry.py"
    source.parent.mkdir(parents=True)
    source.write_text("def test_ok():\n    assert 2 + 2 == 4\n")
    store=Cases(root)
    row=reserve_case(store,"test_ok",{"title":"Entry resolution","description":"Local assertion fixture"},0,"test")
    bind_case(store,row['id'],'verification/test_entry.py','test_ok',owner='test')
    data=store.read()
    data.targets[0].runner=RunTemplate(command="cd {project}/verification && "+shlex.quote(sys.executable)+" -m pytest -q {case}",
                            description="Run from the test directory")
    store.save(data,data.revision)
    runner=Runs()
    try:
        run=await runner.start(root,row["id"])
        assert run["entry"]==str(source)+"::test_ok"
        await asyncio.wait_for(runner.tasks[run["id"]],30)
        assert run["exit_code"]==0,run
        assert run["status"]=="completed"
    finally:
        await runner.close()


def test_template_rejects_prefixing_an_absolute_case():
    """Reject the same entry being wrapped in a second project-relative directory."""
    with pytest.raises(ValueError,match="目录前缀"):
        RunTemplate(command="pytest verification/{case}",description="Invalid entry")
    assert RunTemplate(command="cd {project}/verification && pytest {case}",description="Valid").command
