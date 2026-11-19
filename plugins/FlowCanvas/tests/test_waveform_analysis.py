"""Analyze a tiny real waveform without launching a simulator or a model."""
import json

import pytest
from flowcanvas.waveform_analysis import WaveAnalysisStore


@pytest.fixture
def waveform(tmp_path):
    """Create one terminal execution with known transitions and four-state values."""
    folder=tmp_path/'output/flowcanvas/test-runs';directory=folder/('a'*32)/'waveforms'
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


def test_bug_waveform_analysis_is_scoped_to_linked_runs(waveform):
    """Freeze Bug evidence context and keep each Bug's annotations separate from the case."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from flowcanvas.bugs import execute
    from flowcanvas.case_conversations import CaseView, view_snapshot
    from flowcanvas.testcases import Cases, CasesDefinition, TestCase

    root, run_id = waveform.root, 'a' * 32
    case = TestCase(id='tc_' + 'a' * 32, name='test_related')
    other = TestCase(id='tc_' + 'b' * 32, name='test_other')
    Cases(root).save(CasesDefinition(targets=[case, other]), 0)
    run = root / 'output/flowcanvas/test-runs' / (run_id + '.json')
    run.write_text(json.dumps(dict(id=run_id, case_id=case.id, status='completed')))
    bugs = [execute(root, dict(operation='create', name=f'Bug {revision}', expected_revision=revision,
                               values={'case_ids': [case.id if revision < 2 else other.id]}))
            for revision in range(3)]
    first, second, unrelated = (bug['id'] for bug in bugs)
    project = dict(id='p', path=str(root))
    service = SimpleNamespace(projects=SimpleNamespace(state={'recent': [project]}))
    session = dict(project_root=str(root), case_binding=dict(project_id='p', case_id=first))
    view = CaseView(tab='wave', run_id=run_id, waveform=waveform.waveform, wave_case_id=case.id)
    context = view_snapshot(service, session, view)
    assert context['entity_type'] == 'bug'
    assert context['testcase']['id'] == case.id and context['run']['id'] == run_id
    with pytest.raises(HTTPException, match='Waveform case'):
        view_snapshot(service, session, view.model_copy(update={'wave_case_id': other.id}))

    bug_wave = WaveAnalysisStore(root, first, run_id, waveform.waveform)
    catalog = bug_wave.execute()
    assert {'verdict', 'summary'} <= set(catalog['schema']['required'])
    signal = next(item['name'] for item in catalog['signals'] if item['name'].endswith('req'))
    analysis = dict(verdict='reproduced', summary='Request rose while it was expected to stay low.',
                    signals=[signal], points=[dict(time='5', title='Request', explanation='Request rises', signals=[signal])])
    saved = bug_wave.execute('save', analysis=analysis)
    assert saved['status'] == 'ready'
    assert saved['analysis']['verdict'] == 'reproduced'
    assert saved['analysis']['summary'] == analysis['summary']
    empty = dict(verdict='reproduced', summary='No evidence.', signals=[], points=[])
    with pytest.raises(ValueError, match='waveform evidence point'):
        bug_wave.execute('save', analysis=empty, expected_revision=1)
    saved = bug_wave.execute('save', analysis=dict(empty, verdict='insufficient_evidence'), expected_revision=1)
    assert saved['analysis']['points'] == [] and saved['analysis']['verdict'] == 'insufficient_evidence'
    assert WaveAnalysisStore(root, case.id, run_id, waveform.waveform).read()['status'] == 'empty'
    assert WaveAnalysisStore(root, second, run_id, waveform.waveform).read()['status'] == 'empty'
    with pytest.raises(ValueError, match='linked to this Bug'):
        WaveAnalysisStore(root, unrelated, run_id, waveform.waveform)
