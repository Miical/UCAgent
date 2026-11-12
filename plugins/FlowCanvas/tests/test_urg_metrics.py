"""Native metric parsing preserves real status atoms, instance scope and assertion outcomes."""
import pytest
from flowcanvas.urg_metrics import read_metrics
from flowcanvas.coverage_schema import validate_report


REPORT = """Cond Coverage for Module : Top
Conditions 2 1 50.00
 LINE 12
 EXPRESSION (a & b)
-1- -2- Status
0 1 Covered
1 1 Not Covered

FSM Coverage for Module : Top
Summary for FSM :: state
States 2 2 100.00 (Not included in score)
Transitions 2 1 50.00
Sequences 0 0
State, Transition and Sequence Details for FSM :: state
states Line No. Covered
IDLE 20 Covered
BUSY 21 Covered
transitions Line No. Covered
IDLE->BUSY 21 Covered
BUSY->IDLE 20 Not Covered
Branch Coverage for Module : Top
Branches 2 1 50.00
CASE 30 2 1 50.00
30          case (state)
31            IDLE: x=1;
Branches:
-1- Status
IDLE Covered
default Not Covered

Assert Coverage for Module : Top
Assertions 2 1 50.00 1 50.00
Assertion Details
Name Attempts Real Successes Failures Incomplete
safe 8 4 2 1
inactive 0 0 0 0
"""


def read(header=REPORT, detail="", single=True):
    """Supply a stable explicit source and native instance to the reader."""
    return read_metrics(header, detail, single, "tb.dut", "Top", "Top", "Top.sv", "a"*64, b"safe: assert property(a);\ninactive: assert property(b);\n")


def test_real_atoms_fsm_denominator_and_assertion_failures():
    """Do not score FSM states or confuse attempted assertions with passing assertions."""
    metrics=read()
    assert {k:(v['covered'],v['total']) for k,v in metrics.items()} == {k:(1,2) for k in ['cond','fsm','branch','assert']}
    assert len(metrics['fsm']['states'])==2
    assert metrics['branch']['items'][0]['line']==30
    assertion=metrics['assert']['items'][0]
    assert (assertion['hits'],assertion['attempts'],assertion['successes'],assertion['failures'],assertion['incomplete'])==(1,8,4,2,1)
    report=dict(schema_version=1,build_id='build',code_metrics={k:{'dut':v} for k,v in metrics.items()})
    validate_report(report)
    assertion['failures']=-1
    with pytest.raises(ValueError,match='nonnegative'):
        validate_report(report)


def test_absent_zero_and_instance_specific_observations():
    """Missing instrumentation has no score; repeated modules never use aggregate module data."""
    assert read('')=={}
    assert read('Cond Coverage for Module : Top\nConditions 0 0\n')=={}
    assert read(single=False)=={}
    detail=REPORT.replace('for Module', 'for Instance')
    assert read(header='invalid module aggregation',detail=detail,single=False)['cond']['covered']==1
    unknown=read(REPORT.replace('safe 8', 'expanded_macro 8'))['assert']['items'][0]
    assert unknown['line'] is None


@pytest.mark.parametrize('before,after', [('Conditions 2 1','Conditions 3 1'),('Branches 2 1','Branches 2 2'),('Transitions 2 1','Transitions 3 1'),('Assertions 2 1','Assertions 3 1')])
def test_reject_missing_details_instead_of_fabricating_hits(before,after):
    """Refuse to publish unsupported or truncated detailed reports as trustworthy statistics."""
    with pytest.raises(ValueError,match='native totals'):
        read(REPORT.replace(before,after))
