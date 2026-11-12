"""Five-operation verification card transactions and source binding guarantees."""
import pytest
from flowcanvas.targets import Targets, execute


@pytest.fixture
def call(tmp_path):
    """Call the public card boundary with its current revision."""
    def invoke(operation, owner='alice', **kwargs):
        return execute(tmp_path, {'operation': operation,
                       'expected_revision': Targets(tmp_path).read().revision, **kwargs}, owner)
    return invoke


def test_apply_and_owned_batch_lifecycle(call, tmp_path):
    """Drafts do not animate; only the owning running operation can update them."""
    ids = call('apply', count=2)['ids']
    assert all(t.lifecycle == 'draft' for t in Targets(tmp_path).read().targets)
    for owner in ('alice', 'bob', ''):
        with pytest.raises(ValueError):
            call('update', owner=owner, cards=[{'id': ids[0], 'kind':'group', 'name': 'Premature'}])
    with pytest.raises(ValueError):
        call('begin_modify', owner='bob', ids=ids)
    call('begin_modify', ids=ids)
    call('update', cards=[{'id': i, 'kind':'group', 'name': '功能', 'description': '真实验证说明'} for i in ids])
    call('end_modify', ids=ids, planned=True)
    assert all(t.lifecycle == 'ready' for t in Targets(tmp_path).read().targets)


def test_batch_failure_does_not_partially_update(call, tmp_path):
    """An invalid second card leaves the first and revision unchanged."""
    ids = call('apply', count=2)['ids']
    call('begin_modify', ids=ids)
    before = Targets(tmp_path).path.read_bytes()
    with pytest.raises(ValueError):
        call('update', cards=[{'id': ids[0], 'kind':'group', 'name': 'valid'}, {'id': ids[1], 'parent': ids[0]}])
    assert Targets(tmp_path).path.read_bytes() == before


def test_binding_batch_and_changed_source(call, tmp_path):
    """Binding writes use inspected nodes and changed source must be acknowledged."""
    file = tmp_path/'checks.py'
    file.write_text('def first(x):\n    return x == 1\n\ndef second(x):\n    return x == 2\n')
    ids = call('apply', count=2)['ids']
    call('begin_modify', ids=ids)
    nodes = [1,4]
    call('update', cards=[{'id': i, 'kind':'group', 'name': '检查', 'description':'核对输出', 'bindings':[
        {'file':'checks.py','line':n,'line_count':2,'role':'check'}]}
        for i,n in zip(ids,nodes)])
    call('end_modify', ids=ids)
    call('begin_modify', ids=ids)
    file.write_text(file.read_text().replace('== 1','== 3'))
    with pytest.raises(ValueError, match='bindings'):
        call('end_modify', ids=ids)
    state = call('inspect', ids=[ids[0]])
    binding = state['reports'][ids[0]]['bindings'][0]
    call('update', cards=[{'id':ids[0], 'bindings':[{'id':binding['id'], 'file':'checks.py',
                        'line_count':2,'fingerprint':binding['fingerprint'], 'role':'check'}]}])
    call('end_modify', ids=ids)


def test_source_write_rolls_back_on_final_validation(call,tmp_path,monkeypatch):
    """A save failure restores inserted marker text for the entire batch."""
    file=tmp_path/'checks.py'; file.write_text('def check(x):\n    return x\n')
    identity=call('apply',count=1)['ids'][0]
    call('begin_modify',ids=[identity])
    call('inspect',files=['checks.py'])
    before=Targets(tmp_path).path.read_bytes(); source=file.read_bytes()
    def fail(*args):
        """Simulate a final persistence rejection."""
        raise ValueError('save failed')
    monkeypatch.setattr(Targets,'save',fail)
    with pytest.raises(ValueError,match='save failed'):
        call('update',cards=[{'id':identity,'kind':'group','bindings':[{'file':'checks.py','line':1,'line_count':2}]}])
    assert file.read_bytes()==source
    assert Targets(tmp_path).path.read_bytes()==before


def test_delete_requires_registration_and_explicit_descendants(call,tmp_path):
    """Delete never silently leaves orphan children or permits metadata edits."""
    group=call('apply',count=1)['ids'][0]
    point=call('apply',count=1)['ids'][0]
    call('begin_modify',ids=[group,point])
    call('update',cards=[{'id':group,'kind':'group','name':'组','description':'说明'},
                        {'id':point,'kind':'point','parent':group,'name':'点','description':'说明'}])
    call('end_modify',ids=[group,point],planned=True)
    with pytest.raises(ValueError): call('end_modify',ids=[group])
    with pytest.raises(ValueError): call('begin_modify',ids=[group],action='delete')
    call('begin_modify',ids=[group],action='delete',cascade=True)
    with pytest.raises(ValueError): call('update',cards=[{'id':group,'name':'wrong'}])
    with pytest.raises(ValueError): call('end_modify',ids=[group])
    result=call('end_modify',ids=[group,point])
    assert set(result['deleted'])=={group,point}
    assert not Targets(tmp_path).read().targets


def test_resume_and_foreign_finish(call,tmp_path):
    """Paused work resumes in the same session without new IDs."""
    identity=call('apply',count=1)['ids'][0]
    call('begin_modify',ids=[identity])
    Targets(tmp_path).reconcile_operations({})
    with pytest.raises(ValueError): call('update',cards=[{'id':identity,'description':'暂停'}])
    with pytest.raises(ValueError): call('end_modify',owner='bob',ids=[identity],planned=True)
    call('begin_modify',ids=[identity])
    call('update',cards=[{'id':identity,'kind':'group','name':'功能','description':'说明'}])
    call('end_modify',ids=[identity],planned=True)


def test_unknown_operations_and_stale_revision(call,tmp_path):
    """Removed interfaces and stale writes are rejected rather than translated."""
    for operation in ('create','read','import','bind','accept','begin_update','begin_delete','guide'):
        with pytest.raises(ValueError): call(operation)
    call('apply',count=1)
    with pytest.raises(ValueError,match='Revision'):
        execute(tmp_path,{'operation':'apply','expected_revision':0,'count':1},'alice')


def test_inspect_filters_without_writes(call,tmp_path):
    """Selection and schema discovery are read-only."""
    ids=call('apply',count=2)['ids']
    before=Targets(tmp_path).path.read_bytes()
    result=call('inspect',ids=[ids[0]])
    assert [r['id'] for r in result['targets']]==[ids[0]]
    assert 'request_schema' in result
    assert Targets(tmp_path).path.read_bytes()==before


def test_http_uses_same_contract(tmp_path):
    """The browser API uses the same five operations and rejects legacy writes."""
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from flowcanvas.targets import routes
    app=FastAPI()
    app.include_router(routes(SimpleNamespace(state={'recent':[{'id':'p','path':str(tmp_path)}]})))
    client=TestClient(app)
    path='/api/chat/projects/p/targets'
    result=client.post(path,json={'operation':'apply','count':1}).json()
    identity=result['ids'][0]
    assert client.post(path,json={'operation':'create','name':'old'}).status_code==422
    result=client.post(path,json={'operation':'begin_modify','ids':[identity],
                                 'expected_revision':result['revision']}).json()
    assert result['cards'][0]['lifecycle']=='creating'
    # UI-owned operations are synchronous, not tied to a model session.
    Targets(tmp_path).reconcile_operations({})
    result=client.post(path,json={'operation':'update','cards':[{'id':identity,'kind':'group','name':'功能','description':'说明'}],
                                 'expected_revision':result['revision']}).json()
    assert 'revision' in result
    assert client.post(path,json={'operation':'end_modify','ids':[identity], 'planned':True,
                                  'expected_revision':result['revision']}).status_code==200


def test_ui_task_titles_and_prompts_use_target_contract():
    """Opening the import task must not evaluate its message before inputs exist."""
    from pathlib import Path
    source=(Path(__file__).parents[1]/'src/flowcanvas/web/targets.js').read_text()
    modal=next(line for line in source.splitlines() if 'const {dialog,content}=modal(importCases?' in line)
    assert "importGoals?'导入验证目标'" in modal
    assert 'note.value' not in modal
    for legacy in ('verification_targets(begin_create','verification_targets(read)',
                   'verification_targets(guide','verification_targets(begin_delete'):
        assert legacy not in source


def test_count_only_allocation_and_one_batch_hierarchy(call, tmp_path):
    """Allocate neutral IDs, then assign the entire hierarchy in a single update."""
    result = call('apply', count=3)
    assert set(result) == {'ids', 'revision'}
    group, point, test = result['ids']
    assert all(row.kind is None for row in Targets(tmp_path).read().targets)
    call('begin_modify', ids=result['ids'])
    call('update', cards=[
        {'id': group, 'kind': 'group', 'name': '缓存', 'description': '缓存行为'},
        {'id': point, 'kind': 'point', 'parent': group, 'name': '读取', 'description': '读取行为'},
        {'id': test, 'kind': 'testpoint', 'parent': point, 'name': '命中', 'description': '命中返回数据'},
    ])
    call('end_modify', ids=result['ids'], planned=True)
    assert [row.kind for row in Targets(tmp_path).read().targets] == ['group', 'point', 'testpoint']
    with pytest.raises(ValueError, match='cannot change'):
        call('begin_modify', ids=[group])
        call('update', cards=[{'id': group, 'kind': 'point'}])


def test_count_validation_and_no_implicit_empty_completion(call, tmp_path):
    """An allocated ID cannot be finished until its actual card information is supplied."""
    for count in (0, -1, 501):
        with pytest.raises(ValueError): call('apply', count=count)
    with pytest.raises(ValueError): call('apply', count=1, cards=[{'kind':'group'}])
    identity = call('apply', count=1)['ids'][0]
    call('begin_modify', ids=[identity])
    with pytest.raises(ValueError): call('end_modify', ids=[identity], planned=True)
