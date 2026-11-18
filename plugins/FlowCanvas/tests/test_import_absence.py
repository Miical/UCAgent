"""An evidenced empty import advances every gate without writing cards or running tools."""
import json
import pytest
from flowcanvas.target_import_workflow import TargetImportChecker
from flowcanvas.case_import_workflow import CaseImportChecker
from flowcanvas.import_absence import absent_coverage
from flowcanvas.testcases import Cases


@pytest.fixture
def absence(tmp_path):
    """Provide a real RTL-only project and its independently inspected phase receipts."""
    project=tmp_path/'project';project.mkdir()
    (project/'Adder.v').write_text('module Adder; endmodule\n')
    work=tmp_path/'workspace';(work/'summary').mkdir(parents=True)
    request={'project_root':str(project),'target_baseline':[],'target_snapshot':[],
             'testcase_baseline':[],'testcase_snapshot':[]}
    (tmp_path/'request.json').write_text(json.dumps(request))
    scope={'status':'skipped','skip_reason':'Only DUT RTL exists; no verification implementations',
           'inspected_files':['Adder.v'],'files':[],'card_ids':[],'repairs':[]}
    return project,work,scope


@pytest.mark.parametrize('checker,file',[
    (TargetImportChecker(),'import-scope.json'),(TargetImportChecker(final=True),'import-scope.json'),
    *[(CaseImportChecker(mode=mode),'case-import.json') for mode in ['scope','cards','runners','coverage','smoke']],
])
def test_absent_content_passes_every_phase_gate(absence,checker,file):
    """Empty projects do not need fabricated cards, configuration or smoke receipts."""
    project,work,scope=absence
    (work/'summary'/file).write_text(json.dumps(scope));checker.workspace=str(work)
    accepted,result=checker.do_check()
    assert accepted,result
    assert result['status']=='skipped'
    assert not (project/'.flowcanvas').exists()
    assert not (project/'output').exists()


@pytest.mark.parametrize('change',[{'skip_reason':''},{'inspected_files':[]},
    {'inspected_files':['missing.v']},{'files':['Adder.v']},{'card_ids':['fake']}])
def test_skip_requires_explicit_absence_evidence(absence,change):
    """A skip is not a fallback for an unreadable or incomplete scope."""
    _,work,scope=absence;scope.update(change)
    (work/'summary/case-import.json').write_text(json.dumps(scope))
    checker=CaseImportChecker(mode='smoke');checker.workspace=str(work)
    assert not checker.do_check()[0]


def test_coverage_definition_cannot_be_skipped(absence):
    """Shared or unimported covergroups still require their normal import path."""
    project,work,scope=absence
    (project/'Adder.v').write_text('module Adder; covergroup cg; endgroup endmodule\n')
    (work/'summary/import-scope.json').write_text(json.dumps(scope))
    checker=TargetImportChecker(final=True);checker.workspace=str(work)
    assert not checker.do_check()[0]


def test_skip_rejects_card_changes(absence):
    """An absence receipt may not hide deletion of an existing card."""
    _,work,scope=absence
    request=json.loads((work.parent/'request.json').read_text())
    request['testcase_baseline']=['previous'];request['testcase_snapshot']=[{'id':'previous'}]
    (work.parent/'request.json').write_text(json.dumps(request))
    (work/'summary/case-import.json').write_text(json.dumps(scope))
    checker=CaseImportChecker();checker.workspace=str(work)
    assert not checker.do_check()[0]


def test_absent_coverage_is_separate_from_absent_cases(absence):
    """Real cases may remain importable without an invented functional coverage model."""
    project,_,_=absence
    scope={'coverage_status':'skipped','coverage_skip_reason':'No functional coverage definitions',
           'coverage_inspected_files':['Adder.v']}
    assert absent_coverage(scope,Cases(project))==scope['coverage_skip_reason']
    (project/'Adder.v').write_text('covergroup existing; endgroup\n')
    with pytest.raises(ValueError,match='Coverage declarations exist'):
        absent_coverage(scope,Cases(project))


@pytest.mark.parametrize('mode',['cards','runners','coverage','smoke'])
def test_real_cases_keep_normal_validation_without_coverage(absence,mode):
    """Absent coverage skips only coverage receipts, never the real case or VCD runner gate."""
    from flowcanvas.testcases import execute
    project,work,_=absence
    (project/'test_start.py').write_text('def test_start():\n    assert True\n')
    store=Cases(project)
    def call(operation,**kwargs):
        return execute(project,{'operation':operation,'expected_revision':store.read().revision,**kwargs},'owner')
    identity=call('apply',count=1)['ids'][0]
    call('begin_modify',ids=[identity])
    call('update',cards=[{'id':identity,'name':'test_start','title':'Existing test','description':'Check existing behavior',
        'runner':{'command':'pytest {case} --wave-dir={wave_dir}','description':'Existing fixture with waveform output'},
        'bindings':[{'file':'test_start.py','line':1,'line_count':2}]}])
    call('end_modify',ids=[identity])
    scope={'files':['test_start.py'],'card_ids':[identity], 'coverage_status':'skipped',
           'coverage_skip_reason':'No functional coverage model', 'coverage_inspected_files':['Adder.v','test_start.py']}
    (work/'summary/case-import.json').write_text(json.dumps(scope))
    checker=CaseImportChecker(mode=mode);checker.workspace=str(work)
    accepted,result=checker.do_check()
    assert accepted,result
    if mode in {'coverage','smoke'}:
        assert result['coverage_status']=='skipped' and not result['coverage_smoke_checked']
    (project/'test_start.py').unlink()
    assert not checker.do_check()[0]
