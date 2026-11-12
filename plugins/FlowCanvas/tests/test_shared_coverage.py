"""Shared database publication, smoke isolation and reset lifecycle checks."""
import asyncio
import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from flowcanvas.coverage_runtime import state
from flowcanvas.test_runner import TestRuns as Runs, routes
from flowcanvas.testcases import Cases, CoverageConfig, configure
from test_case_import import make_case


def setup_shared(root):
    """Create a real local command fixture which accumulates into one database."""
    script='''import json, sys
from pathlib import Path
db=Path(sys.argv[1]); db.parent.mkdir(parents=True,exist_ok=True)
hits=int(db.read_text())+1 if db.exists() else 1
db.write_text(str(hits))
print("READY test_start",flush=True)
'''
    identity,call=make_case(root,script)
    case=Cases(root).read().targets[0]
    call('begin_modify',ids=[identity])
    call('update',cards=[dict(id=identity,runner=dict(command=shlex.quote(sys.executable)+' test_start.py {coverage_db} {coverage_name} {case}',description='Shared database fixture'))])
    call('end_modify',ids=[identity])
    adapter=root/'adapter.py'
    adapter.write_text('''import json,sys
from pathlib import Path
db,output=sys.argv[1:]
hits=int(Path(db).read_text())
Path(output).write_text(json.dumps(dict(schema_version=1,build_id="build-A",instances=[dict(path="Probe",covered=min(hits,2),total=2,files=[dict(path="Probe.v",line_hits={"1":hits,"2":max(hits-1,0)})])],functional=dict(observations=[dict(target_id="fixture",binding_fingerprints={"binding":"hash"},bins=[dict(name="bin",hits=hits,goal=1)])]))))
''')
    build=root/'build-id';build.write_text('build-A')
    configure(root,{'coverage':dict(mode='shared',tool='fixture',description='Native cumulative fixture',artifact_path='{run_dir}/coverage.json',extract_command=shlex.quote(sys.executable)+' adapter.py {coverage_db} {run_dir}/coverage.json',build_id_command='cat build-id')},Cases(root).read().revision)
    return identity


@pytest.mark.asyncio
async def test_shared_snapshots_smoke_and_build_guard(tmp_path):
    """Publish current database counts once and prevent incompatible writes."""
    identity=setup_shared(tmp_path);runner=Runs()
    try:
        for expected in (1,2):
            row=await runner.start(tmp_path,identity)
            await asyncio.gather(*runner.tasks.values())
            final=runner.output(tmp_path,row['id'])['run']
            assert final['coverage_status']=='merged',final
            report=state(tmp_path)['report']
            assert report['instances'][0]['files'][0]['line_hits']['1']==expected
            assert report['functional']['observations'][0]['bins'][0]['hits']==expected
        db=Path(row['coverage_db']);assert db.read_text()=='2'
        smoke=await runner.start(tmp_path,identity,'startup_smoke','READY test_start',3)
        await asyncio.gather(*runner.tasks.values())
        assert runner.output(tmp_path,smoke['id'])['run']['smoke_status']=='passed'
        assert Path(smoke['coverage_db']).is_relative_to(Path(smoke['run_dir']))
        assert db.read_text()=='2' and len(state(tmp_path)['runs'])==2
        (tmp_path/'build-id').write_text('build-B')
        failed=await runner.start(tmp_path,identity)
        await asyncio.gather(*runner.tasks.values())
        assert runner.output(tmp_path,failed['id'])['run']['status']=='failed'
        assert db.read_text()=='2'
        assert state(tmp_path)['report']==report
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_clear_deletes_database_and_blocks_active_run(tmp_path):
    """Reset removes physical cumulative state only after the writer stops."""
    identity=setup_shared(tmp_path);runner=Runs()
    app=FastAPI();app.include_router(routes(SimpleNamespace(state={'recent':[dict(id='p',path=str(tmp_path))]}),runner))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        row=await runner.start(tmp_path,identity)
        response=await client.post('/api/chat/projects/p/coverage/reset')
        assert response.status_code==409
        await asyncio.gather(*runner.tasks.values())
        old=state(tmp_path)['epoch'];assert Path(row['coverage_db']).exists()
        source=tmp_path/'.flowcanvas/coverage/sources/hash.v';source.parent.mkdir();source.write_text('snapshot')
        response=await client.post('/api/chat/projects/p/coverage/reset')
        assert response.status_code==200
        assert response.json()['epoch']!=old and response.json()['report'] is None
        assert not Path(row['coverage_db']).exists()
        assert source.exists()
        assert (tmp_path/'.flowcanvas/test-runs'/row['id']).exists()
    await runner.close()


def test_shared_configuration_rejects_snapshot_merge():
    """Cumulative input must not be sent through the independent-run merger."""
    args=dict(mode='shared',tool='vcs',description='Native cumulative',artifact_path='{run_dir}/coverage.json',extract_command='adapter {coverage_db}',build_id_command='identity')
    assert CoverageConfig(**args).mode=='shared'
    with pytest.raises(ValueError):CoverageConfig(**dict(args,merge_command='merge {inputs_manifest} {merge_dir}'))
    with pytest.raises(ValueError):CoverageConfig(**dict(args,extract_command='adapter elsewhere'))


def test_import_coverage_gate_requires_real_adapter_and_runner(tmp_path):
    """Import cannot finish coverage setup with missing code or detached runner arguments."""
    from flowcanvas.case_import_workflow import CaseImportChecker
    from flowcanvas.testcases import execute
    identity=setup_shared(tmp_path)
    work=tmp_path/'workflow/workspace';(work/'summary').mkdir(parents=True)
    (work.parent/'request.json').write_text(json.dumps(dict(project_root=str(tmp_path),testcase_baseline=[])))
    scope=work/'summary/case-import.json'
    plan=dict(files=['test_start.py'],card_ids=[identity],smoke_runs={})
    scope.write_text(json.dumps(plan))
    checker=CaseImportChecker(mode='coverage');checker.workspace=str(work)
    assert not checker.do_check()[0]
    plan['coverage_adapter']='adapter.py';scope.write_text(json.dumps(plan))
    assert checker.do_check()[0]
    execute(tmp_path,dict(operation='begin_modify',ids=[identity],expected_revision=Cases(tmp_path).read().revision),'fixture')
    execute(tmp_path,dict(operation='update',cards=[dict(id=identity,runner=dict(command='python test_start.py {case}',description='Missing coverage arguments'))],expected_revision=Cases(tmp_path).read().revision),'fixture')
    execute(tmp_path,dict(operation='end_modify',ids=[identity],expected_revision=Cases(tmp_path).read().revision),'fixture')
    assert not checker.do_check()[0]
