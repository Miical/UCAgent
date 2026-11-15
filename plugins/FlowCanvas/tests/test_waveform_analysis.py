"""Analyze a tiny real waveform without launching a simulator or a model."""
import json

import pytest
from flowcanvas.waveform_analysis import WaveAnalysisStore


@pytest.fixture
def waveform(tmp_path):
    """Create one terminal execution with known transitions and four-state values."""
    folder=tmp_path/'.flowcanvas/test-runs';directory=folder/('a'*32)/'waveforms'
    directory.mkdir(parents=True)
    (folder/('a'*32+'.json')).write_text(json.dumps(dict(case_id='case',status='completed')))
    path=directory/'waves.vcd'
    path.write_text('''$timescale 1 ps $end
$scope module tb $end
$var wire 1 ! req $end
$var wire 4 " data [3:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 "
#5
1!
b10xz "
#10
0!
b1010 "
''')
    return WaveAnalysisStore(tmp_path,'case','a'*32,'waveforms/waves.vcd')


def test_real_signal_values_points_and_staleness(waveform):
    """Validate states from actual bytes, preserve point ordering and invalidate changed files."""
    catalog=waveform.execute()
    assert catalog['begin']=='0' and catalog['end']=='10'
    names=[s['name'] for s in catalog['signals']]
    req=next(name for name in names if name.endswith('req'))
    data=next(name for name in names if 'data' in name)
    values=waveform.execute('values',signals=[req,data],start='5',end='10')
    assert values['signals'][data]['events'][0]['value']=='10xz'
    payload=dict(signals=[req,data],points=[dict(time='5',title='请求',explanation='请求有效；数据包含未知及高阻位。',confidence='observed',signals=[req,data])])
    saved=waveform.execute('save',analysis=payload)
    assert saved['status']=='ready'
    assert saved['analysis']['points'][0]['values']=={req:'1',data:'10xz'}
    with pytest.raises(ValueError,match='revision'):
        waveform.execute('save',analysis=payload,expected_revision=0)
    bad=dict(payload,points=[dict(payload['points'][0],time='11')])
    with pytest.raises(ValueError,match='bounds'):
        waveform.execute('save',analysis=bad,expected_revision=1)
    bad_window=dict(payload,points=[dict(payload['points'][0],start='6',end='9')])
    with pytest.raises(ValueError,match='window'):
        waveform.execute('save',analysis=bad_window,expected_revision=1)
    with pytest.raises(ValueError,match='both'):
        waveform.execute('save',analysis=dict(payload,points=[dict(payload['points'][0],start='1')]),expected_revision=1)
    with pytest.raises(ValueError,match='Signal not found'):
        waveform.execute('values',signals=['tb.missing'])
    waveform.wave.write_text(waveform.wave.read_text()+'#11\n')
    assert waveform.read()['status']=='stale' and waveform.read()['analysis'] is None


def test_case_and_run_boundaries(waveform):
    """Reject a foreign case, file escape and an unfinished waveform."""
    with pytest.raises(ValueError,match='selected case'):
        WaveAnalysisStore(waveform.root,'other','a'*32,'waveforms/waves.vcd')
    outside=waveform.root/'outside.vcd';outside.write_text('private')
    (waveform.wave.parent/'linked.vcd').symlink_to(outside)
    with pytest.raises(ValueError,match='within this run'):
        WaveAnalysisStore(waveform.root,'case','a'*32,'waveforms/linked.vcd')
    waveform.run['status']='running'
    with pytest.raises(ValueError,match='execution ends'):
        waveform.execute()


@pytest.mark.asyncio
async def test_analysis_endpoint_case_ownership(waveform):
    """Expose persisted analysis through the project API without crossing case boundaries."""
    from types import SimpleNamespace
    from fastapi import FastAPI
    import httpx2
    from flowcanvas.test_runner import TestRuns, routes
    app=FastAPI()
    app.include_router(routes(SimpleNamespace(state={'recent':[dict(id='p',path=str(waveform.root))]}),TestRuns()))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
        endpoint='/api/chat/projects/p/test-runs/'+'a'*32+'/waveform-analysis'
        assert (await client.get(endpoint,params={'case_id':'case'})).json()['status']=='empty'
        assert (await client.get(endpoint,params={'case_id':'other'})).status_code==422
