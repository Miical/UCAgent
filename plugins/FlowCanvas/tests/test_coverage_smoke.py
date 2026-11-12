"""Short complete smoke uses the production runner and validates isolated coverage."""
import asyncio
import json
from pathlib import Path

import pytest
from flowcanvas.case_import_workflow import CaseImportChecker
from flowcanvas.coverage_runtime import state
from flowcanvas.smoke_runs import case_identity
from flowcanvas.targets import Targets
from flowcanvas.test_runner import TestRuns as Runs
from flowcanvas.testcases import Cases, configure, execute
from target_test_support import bind_target, inspect_fixture, register_case, set_case_runners
from test_shared_coverage import setup_shared


def prepare_smoke(root):
    """Configure actual source-bound coverage from a deterministic local fixture."""
    smoke=setup_shared(root)
    (root/'coverage.py').write_text('group = 1\n')
    targets=Targets(root)
    row=targets.create('group','Measured group',None,targets.read().revision,description='Fixture')
    node=inspect_fixture(targets,['coverage.py'])[0]['nodes'][0]
    bind_target(targets,row['id'],dict(file='coverage.py',start=node['start'],fingerprint=node['fingerprint'],role='coverage'),targets.read().revision)
    target=targets.read().targets[0]
    adapter=root/'adapter.py'
    text=adapter.read_text().replace('target_id="fixture",binding_fingerprints={"binding":"hash"}',
        'target_id='+repr(target.id)+',binding_fingerprints='+repr({b.id:b.fingerprint for b in target.bindings}))
    adapter.write_text(text)
    source=root/'reference.py';source.write_text('def test_reference():\n    pass\n')
    register_case(Cases(root),'reference.py','test_reference','Reference','Existing case')
    reference=Cases(root).read().targets[-1].id
    set_case_runners(Cases(root),Cases(root).read().targets[0].runner.model_dump())
    return smoke,reference


def import_gate(root,smoke,reference):
    """Require one complete representative smoke, not a receipt for every imported case."""
    work=root/'task/workspace';(work/'summary').mkdir(parents=True)
    (work.parent/'request.json').write_text(json.dumps(dict(project_root=str(root),testcase_baseline=[])))
    plan=dict(files=['test_start.py','reference.py'],card_ids=[smoke,reference],smoke_case_id=smoke,smoke_reference_case_id=reference)
    path=work/'summary/case-import.json';path.write_text(json.dumps(plan))
    checker=CaseImportChecker(mode='smoke');checker.workspace=str(work)
    return checker,path,plan


@pytest.mark.asyncio
async def test_complete_smoke_checks_coverage_and_keeps_accumulation(tmp_path):
    """Natural completion and real mapping succeed without changing formal coverage state."""
    smoke,reference=prepare_smoke(tmp_path)
    before=state(tmp_path)
    checker,path,plan=import_gate(tmp_path,smoke,reference)
    assert not checker.do_check()[0]
    runner=Runs()
    try:
        row=await runner.start(tmp_path,smoke,'coverage_smoke','READY test_start',3)
        await asyncio.gather(*runner.tasks.values())
        result=runner.output(tmp_path,row['id'])['run']
        assert result['status']=='completed' and result['smoke_status']=='passed',result
        assert result['coverage_status']=='validated' and result['exit_code']==0
        assert result['stop_reason']=='natural_exit'
        assert Path(result['coverage_db']).is_relative_to(Path(result['run_dir']))
        assert state(tmp_path)==before
        assert not (tmp_path/'.flowcanvas/coverage/database').exists()
        plan['smoke_run_id']=row['id'];path.write_text(json.dumps(plan))
        assert checker.do_check()[0],checker.do_check()
        config=Cases(tmp_path).read().coverage.model_dump();config['description']='Changed'
        configure(tmp_path,{'coverage':config},Cases(tmp_path).read().revision)
        assert not checker.do_check()[0]
    finally:
        await runner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['extract','line','functional','mapping','exit','timeout','readiness','errors'])
async def test_full_smoke_failure_boundaries(tmp_path,failure):
    """Startup alone, broken extraction and incomplete runs cannot pass import acceptance."""
    smoke,_=prepare_smoke(tmp_path)
    adapter=tmp_path/'adapter.py';text=adapter.read_text()
    if failure=='extract':adapter.write_text('raise RuntimeError("conversion failed")\n')
    elif failure=='line':adapter.write_text(text.replace('covered=min(hits,2),total=2','covered=0,total=0'))
    elif failure=='functional':adapter.write_text(text.replace('name="bin",hits=hits,goal=1','name="bin",hits=hits,goal=1').replace('functional=dict(observations=[','functional=dict(observations=[] if True else ['))
    elif failure=='mapping':adapter.write_text(text.replace('binding_fingerprints={','binding_fingerprints={"unexpected":"hash",'))
    if failure in {'exit','timeout','errors'}:
        source=tmp_path/'test_start.py'
        suffix='\nraise SystemExit(2)\n' if failure=='exit' else '\nimport time;time.sleep(30)\n' if failure=='timeout' else '\nprint("UVM_ERROR: broken check")\n'
        source.write_text(source.read_text()+suffix)
        # Existing bound range is unchanged; appended diagnostics belong to execution.
    runner=Runs();before=state(tmp_path)
    try:
        row=await runner.start(tmp_path,smoke,'coverage_smoke','MISSING' if failure=='readiness' else 'READY test_start',1)
        await asyncio.wait_for(asyncio.gather(*runner.tasks.values()),6)
        result=runner.output(tmp_path,row['id'])['run']
        assert result['smoke_status']=='failed',result
        assert state(tmp_path)==before
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_startup_only_receipt_is_rejected(tmp_path):
    """Legacy kill-after-ready cannot satisfy the new import gate."""
    smoke,reference=prepare_smoke(tmp_path)
    checker,path,plan=import_gate(tmp_path,smoke,reference)
    runner=Runs()
    try:
        row=await runner.start(tmp_path,smoke,'startup_smoke','READY test_start',3)
        await asyncio.gather(*runner.tasks.values())
        plan['smoke_run_id']=row['id'];path.write_text(json.dumps(plan))
        assert not checker.do_check()[0]
    finally:
        await runner.close()
