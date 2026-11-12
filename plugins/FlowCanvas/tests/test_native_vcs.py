"""Platform-owned database ingestion without per-project extraction commands."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from flowcanvas.coverage_runtime import reset,state
from flowcanvas.coverage_watch import CoverageWatch
from flowcanvas.native_vcs import build_identity,extract,fingerprint,map_functional
from flowcanvas.targets import atomic
from flowcanvas.test_runner import TestRuns as Runs
from flowcanvas.testcases import CoverageConfig


def native_project(root):
    """Create native report fixtures and a local stand-in for the URG command protocol."""
    folder=root/'.flowcanvas/coverage';folder.mkdir(parents=True)
    source=root/'Top.sv';source.write_text('module Top(input a);\nassign y=a;\nendmodule\n')
    binary=root/'simv';binary.write_text('binary-A')
    atomic(root/'.flowcanvas/project.json',dict(rtl=dict(top='Top',files=['Top.sv'])))
    atomic(folder/'build.json',dict(inputs=['simv']))
    database=folder/'database/coverage.vdb';tests=database/'snps/coverage/db/testdata/one';tests.mkdir(parents=True)
    (tests/'siminfo.xml').write_text('complete')
    output=f'''Module : Top
Source File(s) :
{source}
Module self-instances :
SCORE LINE TOGGLE NAME
100.00 100.00 1/1 100.00 2/2 tb.dut
Line Coverage for Module : Top
2 1/1 assign y=a;
Toggle Coverage for Module : Top
Total Bits 2 2 100.00
Port Details
a Yes Yes Yes INPUT
Module Instance : tb.dut
Instance :
'''
    urg=root/'urg';urg.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nassert "-nomdacmp" in sys.argv\np=Path(sys.argv[sys.argv.index("-report")+1]);p.mkdir(parents=True,exist_ok=True)\n(p/"modinfo.txt").write_text('+repr(output)+')\n');urg.chmod(0o755)
    atomic(folder/'config.json',dict(provider='vcs',dut_instance='tb.dut',urg_executable=str(urg)))
    return folder,database


@pytest.mark.asyncio
async def test_native_extract_uses_fixed_metadata_and_current_sources(tmp_path):
    """No custom extract/build commands are needed for actual subprocess conversion."""
    folder,database=native_project(tmp_path)
    config=CoverageConfig(provider='vcs',tool='vcs-urg',description='Native')
    assert config.extract_command==config.build_id_command==''
    old_tests=folder/'database/compile.vdb/snps/coverage/db/testdata/old'
    old_tests.mkdir(parents=True);(old_tests/'counter').write_text('old smoke data')
    design=folder/'database/compile.vdb/snps/coverage/db/design';design.mkdir()
    (design/'metadata').write_text('compiled')
    report=await extract(tmp_path,database,folder/'reports/one/coverage.json',build_identity(tmp_path))
    copied=folder/'reports/one/compile.vdb/snps/coverage/db'
    assert (copied/'design/metadata').exists() and not (copied/'testdata').exists()
    assert report['instances'][0]['path']=='Top'
    assert report['instances'][0]['files'][0]['covered_lines']==[2]
    assert report['code_metrics']['toggle']['dut']['covered']==2
    with pytest.raises(ValueError):CoverageConfig(provider='vcs',tool='vcs',description='Native',extract_command='custom')
    atomic(folder/'build.json',dict(inputs=['../outside']))
    with pytest.raises(ValueError,match='escapes'):build_identity(tmp_path)


@pytest.mark.asyncio
async def test_watch_debounce_external_updates_errors_and_reset(tmp_path):
    """Stable external writes trigger extraction; failures retain the accepted panel data."""
    folder,database=native_project(tmp_path);runner=Runs()
    watch=CoverageWatch(SimpleNamespace(state={'recent':[dict(path=str(tmp_path))]}),runner,settle_seconds=0)
    await watch.tick(tmp_path);assert state(tmp_path)['report'] is None
    await watch.tick(tmp_path);first=state(tmp_path)
    assert first['report']['instances'][0]['covered']==1
    assert first['database_fingerprint']==fingerprint(tmp_path)
    assert len(first['runs'])==1
    (database/'snps/coverage/db/testdata/two').mkdir()
    (database/'snps/coverage/db/testdata/two/siminfo.xml').write_text('complete')
    await watch.tick(tmp_path);await watch.tick(tmp_path)
    assert len(state(tmp_path)['runs'])==2
    # A compiler change invalidates publication instead of mixing old/new builds.
    (tmp_path/'simv').write_text('binary-B')
    await watch.tick(tmp_path);await watch.tick(tmp_path)
    failed=state(tmp_path)
    assert failed['ingestion_status']=='failed'
    assert failed['report']==first['report']
    compile_db=folder/'database/compile.vdb';compile_db.mkdir()
    reset(tmp_path)
    assert not database.exists() and compile_db.exists()
    assert all((folder/name).exists() for name in ('config.json','build.json','sources'))
    assert state(tmp_path)['report'] is None


@pytest.mark.asyncio
async def test_watch_waits_for_managed_writer_and_retries_failed_extraction(tmp_path,monkeypatch):
    """No extraction while a managed writer is active; retry follows changed input."""
    folder,database=native_project(tmp_path);runner=Runs();watch=CoverageWatch(None,runner,0)
    row=dict(id='active',status='running',started=1)
    atomic(runner.folder(tmp_path)/'active.json',row)
    runner.tasks['active']=object()
    await watch.tick(tmp_path);assert not watch.observed
    runner.tasks.clear()
    original=extract;broken=AsyncMock(side_effect=ValueError('bad native report'))
    monkeypatch.setattr('flowcanvas.coverage_watch.extract',broken)
    await watch.tick(tmp_path);await watch.tick(tmp_path)
    assert state(tmp_path)['ingestion_status']=='failed'
    await watch.tick(tmp_path);assert broken.await_count==1
    monkeypatch.setattr('flowcanvas.coverage_watch.extract',original)
    (database/'new-counter').write_text('updated')
    await watch.tick(tmp_path);await watch.tick(tmp_path)
    assert state(tmp_path)['ingestion_status']=='ready'
    assert 'ingestion_error' not in state(tmp_path)


@pytest.mark.asyncio
async def test_changed_snapshot_during_extraction_is_not_published(tmp_path,monkeypatch):
    """A concurrent external writer cannot publish a mixed snapshot."""
    folder,database=native_project(tmp_path);watch=CoverageWatch(None,Runs(),0)
    async def writing(*args):
        """Change native input after report generation, as a simulator would."""
        report=await extract(*args)
        (database/'counter').write_text('changed')
        return report
    monkeypatch.setattr('flowcanvas.coverage_watch.extract',writing)
    await watch.tick(tmp_path);await watch.tick(tmp_path)
    assert state(tmp_path)['report'] is None


@pytest.mark.asyncio
async def test_managed_native_run_uses_builtin_extractor_and_deduplicates_watch(tmp_path):
    """A normal card run needs no custom identity/extract commands and publishes only once."""
    import asyncio,shlex,sys
    from flowcanvas.testcases import Cases,configure
    from test_case_import import make_case
    from target_test_support import set_case_runners
    folder,database=native_project(tmp_path)
    identity,_=make_case(tmp_path,'import sys\nfrom pathlib import Path\np=Path(sys.argv[1])/"counter"\np.write_text("fresh")\nprint("READY test_start")\n')
    set_case_runners(Cases(tmp_path),dict(command=shlex.quote(sys.executable)+' test_start.py {coverage_db} {coverage_name} {case}',description='Native fixture'))
    configure(tmp_path,{'coverage':dict(provider='vcs',tool='vcs-urg',description='Native')},Cases(tmp_path).read().revision)
    runner=Runs();watch=CoverageWatch(None,runner,0)
    row=await runner.start(tmp_path,identity)
    await asyncio.gather(*runner.tasks.values())
    result=runner.output(tmp_path,row['id'])['run']
    assert result['coverage_status']=='merged',result
    assert result['status']=='completed'
    before=state(tmp_path)
    await watch.tick(tmp_path);await watch.tick(tmp_path)
    assert state(tmp_path)==before


@pytest.mark.asyncio
async def test_watch_skips_external_live_writer(tmp_path,monkeypatch):
    """External VCS writers receive the same quiescence protection as managed tests."""
    native_project(tmp_path);watch=CoverageWatch(None,Runs(),0)
    monkeypatch.setattr('flowcanvas.coverage_watch.writer_active',lambda _:True)
    await watch.tick(tmp_path);await watch.tick(tmp_path)
    assert state(tmp_path)['report'] is None and not watch.observed


def test_native_mapping_preserves_counts_and_rejects_ambiguous_instances(tmp_path):
    """Mappings use exact native instance/source identity and frozen panel bindings."""
    import hashlib
    folder,_=native_project(tmp_path)
    raw=(tmp_path/'Top.sv').read_bytes();entry=dict(target_id='vt_fixture',selector=dict(type='scope::cg'),
        file='Top.sv',sha256=hashlib.sha256(raw).hexdigest(),binding_fingerprints={'binding':'frozen'})
    atomic(folder/'targets.json',[entry])
    group=dict(type='scope::cg',instance='one',source=str(tmp_path/'Top.sv'),
        points=[dict(name='cp',covered=1,total=2,bins=[],reported_bins=[])])
    report,warnings=map_functional(tmp_path,[group])
    assert report['observations'][0]['covered']==1
    assert report['observations'][0]['total']==2
    assert report['observations'][0]['bins']==[]
    with pytest.raises(ValueError,match='exactly one'):
        map_functional(tmp_path,[group,dict(group,instance='two')])
    entry['selector']['instance']='one';atomic(folder/'targets.json',[entry])
    assert map_functional(tmp_path,[group,dict(group,instance='two')])[0]==report
    entry['sha256']='changed';atomic(folder/'targets.json',[entry])
    with pytest.raises(ValueError,match='Compiled coverage source'):
        map_functional(tmp_path,[group])
