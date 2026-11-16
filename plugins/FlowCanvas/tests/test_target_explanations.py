"""Target understanding metadata must remain grounded, transactional and independent of test execution."""
import copy
import pytest
from flowcanvas.targets import Targets, execute
from test_case_import import make_case


@pytest.fixture
def setup(tmp_path):
    """Create a real group/point/testpoint hierarchy and source bindings through owned operations."""
    source=tmp_path/'checks.py';source.write_text('def check(x):\n    return x == 1\n')
    def call(operation,owner='fixture',**values):
        """Apply one public request using the latest definition revision."""
        return execute(tmp_path,dict(operation=operation,expected_revision=Targets(tmp_path).read().revision,**values),owner)
    group,point,testpoint=call('apply',count=3)['ids'];call('begin_modify',ids=[group,point,testpoint])
    call('update',cards=[dict(id=group,kind='group',name='Data checks',description='Organize comparison checks'),dict(id=point,kind='point',parent=group,name='Value comparison',description='Check comparison behavior'),dict(id=testpoint,kind='testpoint',parent=point,name='Value equals one',description='Check the matching value',bindings=[dict(file='checks.py',line=1,line_count=2,role='check')])]);call('end_modify',ids=[group,point,testpoint],planned=True)
    binding=call('inspect',ids=[testpoint])['reports'][testpoint]['bindings'][0]
    explanation=dict(summary='Check the expected value',conditions='A value is supplied',expected='Only one matches',check_kind='comparison',implementation=[dict(title='Compare the value',text='The expression compares x with one',confidence='source',assignments=[dict(file='checks.py',line=binding['line']+1,expression='return x == 1',meaning='Return the comparison result')])],coverage_notes='A hit alone is not correctness evidence')
    return tmp_path,call,group,point,testpoint,explanation


def test_explanation_roundtrip_and_source_staleness(setup):
    """Persist feature-specific explanations, preserve other fields and detect source edits."""
    root,call,group,point,testpoint,explanation=setup
    before=Targets(root).read();source=(root/'checks.py').read_bytes()
    state=call('inspect',ids=[testpoint]);assert 'explanation_schema' in state and '功能点与测试点' in state['explanation_guide']
    case_id,_=make_case(root,'def test_start():\n    assert True\n')
    explanation['related_cases']=[dict(case_id=case_id,reason='Explicitly selected by the author')]
    explanation['source_hashes']={'../forged.py':'forged'}
    call('begin_modify',ids=[point,testpoint]);call('update',cards=[dict(id=point,explanation=dict(summary='Explain the feature',approach='Check normal and boundary values')),dict(id=testpoint,explanation=explanation)]);call('end_modify',ids=[point,testpoint],planned=True)
    saved=Targets(root).read().targets[-1]
    assert saved.explanation.related_cases[0].case_id==case_id
    assert set(saved.explanation.source_hashes)=={'checks.py'}
    assert saved.name==before.targets[-1].name and saved.bindings==before.targets[-1].bindings
    assert (root/'checks.py').read_bytes()==source
    assert call('inspect',ids=[testpoint])['reports'][testpoint]['explanation_status']=='available'
    (root/'checks.py').write_text((root/'checks.py').read_text().replace('== 1','== 2'))
    assert call('inspect',ids=[testpoint])['reports'][testpoint]['explanation_status']=='stale'


@pytest.mark.parametrize('bad',['expression','outside','range','case','source_claim','check_kind'])
def test_reject_unfounded_explanations_atomically(setup,bad):
    """Reject fabricated code, paths, ranges, cases and unsupported checking claims without partial writes."""
    root,call,group,point,testpoint,explanation=setup
    value=copy.deepcopy(explanation);assignment=value['implementation'][0]['assignments'][0]
    if bad=='expression':assignment['expression']='return fabricated_result'
    elif bad=='outside':assignment['file']='../outside.py'
    elif bad=='range':assignment['end_line']=10000
    elif bad=='case':value['related_cases']=[dict(case_id='tc_'+'f'*32,reason='Unverified relationship')]
    elif bad=='source_claim':value['implementation'][0]['assignments']=[];value['check_kind']='unknown'
    elif bad=='check_kind':value['implementation']=[]
    call('begin_modify',ids=[testpoint]);before=Targets(root).path.read_bytes();source=(root/'checks.py').read_bytes()
    with pytest.raises(ValueError):call('update',cards=[dict(id=testpoint,explanation=value)])
    assert Targets(root).path.read_bytes()==before and (root/'checks.py').read_bytes()==source


def test_explanation_writes_keep_ownership_and_group_role(setup):
    """Descriptions cannot take over another session's work or turn a group into a detail entity."""
    root,call,group,point,testpoint,explanation=setup
    call('begin_modify',ids=[testpoint])
    with pytest.raises(ValueError):call('update',owner='other',cards=[dict(id=testpoint,explanation=explanation)])
    call('begin_modify',ids=[group])
    with pytest.raises(ValueError,match='Only points'):call('update',cards=[dict(id=group,explanation=dict(summary='No group details'))])


def test_explanation_failure_rolls_back_new_binding_markers(setup):
    """Rejecting an explanation restores source comments inserted earlier in the same atomic batch."""
    root,call,group,point,testpoint,explanation=setup
    call('begin_modify',ids=[point]);before=Targets(root).path.read_bytes();source=(root/'checks.py').read_bytes()
    value=copy.deepcopy(explanation);value['implementation'][0]['assignments'][0]['expression']='return invented_value'
    with pytest.raises(ValueError):call('update',cards=[dict(id=point,bindings=[dict(file='checks.py',line=3,line_count=1,role='check')],explanation=value)])
    assert Targets(root).path.read_bytes()==before and (root/'checks.py').read_bytes()==source
