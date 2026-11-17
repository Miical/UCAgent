"""Formal test results require framework evidence and stay bound to the accepted case version."""
import json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flowcanvas.run_results import test_result as evaluate
from flowcanvas.smoke_runs import case_identity
from flowcanvas.targets import atomic
from flowcanvas.testcases import Cases, execute
from flowcanvas.test_runner import TestRuns as Runs, routes


@pytest.mark.parametrize('status,exit_code,text,expected',[
    ('running',None,'','running'),
    ('completed',0,'UVM_ERROR : 0\nUVM_FATAL : 0\n','passed'),
    ('completed',0,'TEST CASE PASSED\n','passed'),
    ('completed',0,'================ 2 passed in 0.02s ================','passed'),
    ('completed',0,'UVM_ERROR : 1\nUVM_FATAL : 0\n','failed'),
    ('completed',0,'TEST PASSED\nUVM_ERROR @ 10: mismatch\n','failed'),
    ('completed',0,'assertion p_handshake failed\n','failed'),
    ('completed',0,'1 failed, 2 passed in 0.1s','failed'),
    ('failed',1,'','failed'),
    ('interrupted',None,'TEST PASSED','failed'),
    ('completed',0,'simulation ended\n',None),
])
def test_formal_outcomes(status,exit_code,text,expected):
    """Zero exit alone cannot pass and explicit failures override pass markers."""
    result=evaluate(dict(status=status,exit_code=exit_code),text)
    assert result['status']==expected
    assert result['reason']


@pytest.mark.parametrize('mode',['startup_smoke','coverage_smoke'])
def test_smoke_is_not_formal_success(mode):
    """A successful smoke never becomes a formal case result."""
    assert evaluate(dict(mode=mode,status='completed',exit_code=0),'TEST PASSED')['status'] is None


def test_result_api_and_version_freshness(tmp_path):
    """History remains readable while list badges require current accepted source and runner."""
    source=tmp_path/'test_case.py';source.write_text('def test_case():\n    assert True\n')
    store=Cases(tmp_path)
    def call(operation,**values):
        """Use the public case lifecycle with current revisions."""
        return execute(tmp_path,dict(operation=operation,expected_revision=store.read().revision,**values),'fixture')
    identity=call('apply',count=1)['ids'][0];call('begin_modify',ids=[identity])
    call('update',cards=[dict(id=identity,name='test_case',title='Fixture',description='Result fixture',runner=dict(command='python {case}',description='Fixture'),bindings=[dict(file=source.name,line=1,line_count=2)])])
    call('end_modify',ids=[identity])
    runner=Runs();folder=runner.folder(tmp_path);run_id='a'*32
    record=dict(id=run_id,case_id=identity,status='completed',exit_code=0,started=1,case_identity=case_identity(store.read().targets[0]))
    atomic(folder/(run_id+'.json'),record);(folder/(run_id+'.log')).write_text('UVM_ERROR : 0\nUVM_FATAL : 0\n')
    app=FastAPI();app.include_router(routes(SimpleNamespace(state={'recent':[dict(id='p',path=str(tmp_path))]}),runner))
    with TestClient(app) as client:
        row=client.get('/api/chat/projects/p/test-runs').json()[0]
        assert row['test_result']['status']=='passed' and row['result_current']
        assert runner.output(tmp_path,run_id)['run']['test_result']['status']=='passed'
        call('begin_modify',ids=[identity]);call('update',cards=[dict(id=identity,runner=dict(command='python -u {case}',description='Changed'))]);call('end_modify',ids=[identity])
        assert not client.get('/api/chat/projects/p/test-runs').json()[0]['result_current']
        record['case_identity']=case_identity(store.read().targets[0]);atomic(folder/(run_id+'.json'),record)
        assert client.get('/api/chat/projects/p/test-runs').json()[0]['result_current']
        source.write_text(source.read_text().replace('assert True','assert False'))
        assert not client.get('/api/chat/projects/p/test-runs').json()[0]['result_current']
        # Log changes invalidate the derived result cache; stored execution metadata is untouched.
        (folder/(run_id+'.log')).write_text('TEST FAILED\n')
        assert runner.output(tmp_path,run_id)['run']['test_result']['status']=='failed'
        assert json.loads((folder/(run_id+'.json')).read_text())==record
