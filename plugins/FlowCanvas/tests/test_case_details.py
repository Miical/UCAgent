"""Execution detail APIs expose isolated records and confine waveform access to one run."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
import httpx2
from flowcanvas.test_runner import TestRuns as Runs, routes


@pytest.mark.asyncio
async def test_case_execution_filter_and_waveform_confinement(tmp_path):
    """Include smoke only when requested, and never expose another run or symlink target."""
    runner=Runs();folder=runner.folder(tmp_path)
    identity='a'*32;directory=folder/identity;directory.mkdir()
    record=dict(id=identity,case_id='tc_one',status='completed',started=1,mode='coverage_smoke')
    (folder/(identity+'.json')).write_text(json.dumps(record))
    (directory/'dump.vcd').write_text('$enddefinitions $end')
    (directory/'notes.txt').write_text('private')
    outside=tmp_path/'outside.vcd';outside.write_text('outside')
    (directory/'linked.vcd').symlink_to(outside)
    (directory/'linked-directory').symlink_to(tmp_path,target_is_directory=True)
    app=FastAPI();app.include_router(routes(SimpleNamespace(state={'recent':[dict(id='p',path=str(tmp_path))]}),runner))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
        base='/api/chat/projects/p/test-runs'
        assert (await client.get(base)).json()==[]
        rows=(await client.get(base,params=dict(case_id='tc_one',include_smoke=True))).json()
        assert [row['id'] for row in rows]==[identity]
        assert (await client.get(base,params=dict(case_id='tc_other',include_smoke=True))).json()==[]
        files=(await client.get(base+'/'+identity+'/waveforms')).json()['files']
        assert [file['path'] for file in files]==['dump.vcd']
        response=await client.get(base+'/'+identity+'/waveform',params={'path':'dump.vcd'})
        assert response.status_code==200 and response.text=='$enddefinitions $end'
        inline=base+'/'+identity+'/waveform-view/'
        response=await client.get(inline+'dump.vcd')
        assert response.status_code==200 and response.text=='$enddefinitions $end'
        assert 'content-disposition' not in response.headers
        (directory/'nested').mkdir()
        (directory/'nested/dump.fst').write_bytes(b'fst fixture')
        assert (await client.get(inline+'nested/dump.fst')).content==b'fst fixture'
        assert (await client.get(inline+'linked.vcd')).status_code==403
        assert (await client.get(inline+'linked-directory/outside.vcd')).status_code==403
        assert (await client.get(inline+'%2e%2e/outside.vcd')).status_code==403
        assert (await client.get(inline+'notes.txt')).status_code==404
        (directory/'dump.fsdb').write_bytes(b'fsdb fixture')
        assert (await client.get(inline+'dump.fsdb')).status_code==415
        assert (await client.get(base+'/'+'b'*32+'/waveform-view/dump.vcd')).status_code==404

        for name in ('../outside.vcd','linked.vcd'):
            assert (await client.get(base+'/'+identity+'/waveform',params={'path':name})).status_code==403
        assert (await client.get(base+'/'+identity+'/waveform',params={'path':'notes.txt'})).status_code==404
        assert (await client.get(base+'/'+'b'*32+'/waveforms')).status_code==404
        assert (await client.get(base+'/bad/waveforms')).status_code==400


@pytest.mark.asyncio
async def test_bundled_surfer_assets_and_isolation(tmp_path):
    """Serve the real viewer and WASM with isolation on the embedding page and viewer."""
    from flowcanvas.server import create_app
    app=create_app(state_dir=tmp_path/'state')
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://testserver') as client:
        for path in ('/','/rtl-viewer/','/surfer/','/surfer/surfer_bg.wasm','/surfer/fst-converter.wasm','/surfer/fst-fallback-worker.js'):
            response=await client.get(path)
            assert response.status_code==200
            assert response.headers['cross-origin-embedder-policy']=='require-corp'
            assert response.headers['cross-origin-opener-policy']=='same-origin'
            if path.endswith('.wasm'):
                assert response.content.startswith(b'\x00asm')
                assert response.headers['content-type']=='application/wasm'


@pytest.mark.asyncio
async def test_wave_directory_expansion_without_simulation(tmp_path,monkeypatch):
    """Reserve one run and render its waveform path without executing a project command."""
    import asyncio
    import shlex
    from test_case_import import make_case
    from flowcanvas.testcases import Cases
    identity,call=make_case(tmp_path,'def test_start():\n    assert True\n')
    call('begin_modify',ids=[identity])
    call('update',cards=[dict(id=identity,runner=dict(command='echo {case} {wave_dir}',description='Static path fixture'))])
    call('end_modify',ids=[identity])
    runner=Runs()
    async def record_only(folder,row):
        """Avoid spawning even a fixture command while checking path allocation."""
        return None
    monkeypatch.setattr(runner,'execute',record_only)
    row=await runner.start(tmp_path,identity)
    await asyncio.gather(*runner.tasks.values())
    wave_dir=tmp_path/'.flowcanvas/test-runs'/row['id']/'waveforms'
    assert wave_dir.is_dir()
    assert row['wave_dir']==str(wave_dir)
    assert shlex.split(row['command'])[-1]==str(wave_dir)
    assert '{wave_dir}' not in row['command']
