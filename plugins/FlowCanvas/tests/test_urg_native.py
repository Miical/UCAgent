"""Native URG identities and aggregate observations remain truthful on the panel."""
import json
import pytest
from flowcanvas.urg_native import read_groups
from flowcanvas.coverage_schema import validate_report
from flowcanvas.coverage_adapter import merge_reports
from flowcanvas.targets import atomic
from test_coverage_adapters import coverage_project

REPORT='''Group : scope.gen[3]::cover_cg_2
Source File(s) :
/build/original.sv
1 Instances:
Group Instance : original_cg
Variables for Group Instance original_cg
VARIABLE EXPECTED UNCOVERED COVERED PERCENT GOAL WEIGHT AT LEAST
value 2 1 1 50.00 100 1 1
mix 8 6 2 25.00 100 1 1
Summary for Variable value
User Defined Bins 2 1 1 50.00
NAME COUNT AT LEAST SUBSUMED VALUES
zero 0 1 --
one 5 1 --
Summary for Cross mix
Automatically Generated Cross Bins 8 6 2 25.00
Element holes
value other COUNT AT LEAST NUMBER
* * -- -- 6
Covered bins
value other COUNT AT LEAST
zero one 4 1
one zero 5 1
'''


def test_generated_type_uses_native_source_and_counts():
    """Generated names are not interpreted as declarations; compressed bins stay aggregate."""
    group,=read_groups(REPORT)
    assert group['type']=='scope.gen[3]::cover_cg_2'
    assert group['source']=='/build/original.sv'
    assert group['instance']=='original_cg'
    value,mix=group['points']
    assert len(value['bins'])==2
    assert mix['covered']==2 and mix['total']==8 and mix['bins']==[]


def test_multiple_instances_remain_separate():
    """Never select one instance or silently take a union."""
    text=REPORT
    with pytest.raises(ValueError,match='instance details'):
        read_groups(text.replace('1 Instances:','2 Instances:'))


def test_native_aggregate_projection_and_merge_guard(coverage_project):
    """Project native totals using frozen bindings; reject percentage-like merging."""
    root,store,ids,_=coverage_project
    card=store.read().targets[-1]
    report=dict(schema_version=1,build_id='build',instances=[],functional=dict(observations=[dict(
        target_id=card.id,binding_fingerprints={b.id:b.fingerprint for b in card.bindings},
        covered=2,total=8,bins=[])]))
    validate_report(report)
    atomic(root/'.flowcanvas/coverage/state.json',dict(epoch='one',runs=[],report=report))
    item=store.status()['coverage']['targets'][card.id]['observations'][0]
    assert (item['covered'],item['total'],item['bins'])==(2,8,[])
    paths=[root/'one.json',root/'two.json']
    for path in paths:path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='native database'):
        merge_reports(paths)
    report['functional']['observations'][0]['covered']=9
    with pytest.raises(ValueError):validate_report(report)
