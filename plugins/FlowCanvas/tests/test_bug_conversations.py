"""Bug tabs retain scoped histories and keep simulated demo records distinct from real evidence."""
import json
import pytest
import httpx2
from fastapi import FastAPI, HTTPException
from flowcanvas.bugs import Bugs, execute
from flowcanvas.chat import ChatService
from flowcanvas.case_conversations import routes, CaseView, view_snapshot


@pytest.mark.asyncio
async def test_bug_tabs_and_demo_provenance(tmp_path):
    """Open demo details without model execution and reject unsupported runtime or foreign-source context."""
    root=tmp_path/'project';root.mkdir()
    first=execute(root,dict(operation='create',name='Demo missing data',expected_revision=0,values=dict(example=True,description='Hypothetical UI example',condition='Hypothetical burst',expected='All bytes retained',actual='Assumed missing byte; not reproduced',evidence='No simulation evidence')))
    second=execute(root,dict(operation='create',name='Demo stale flag',expected_revision=Bugs(root).read().revision,values=dict(example=True)))
    service=ChatService(tmp_path/'state');project=dict(id='p',path=str(root));(root/'.flowcanvas/project.json').write_text(json.dumps({'schema_version':1,'name':'fixture',**project}));service.projects.activate(project);app=FastAPI();app.include_router(routes(service))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://test') as client:
        url='/api/chat/projects/p/bugs/'+first['id']+'/conversation'
        assert (await client.get(url)).json()=={'session':None}
        session=(await client.post(url,json={})).json();assert session['case_binding']['case_id']==first['id']
        another=(await client.post(url,json={'draft_id':'e'*32})).json();assert another['id']!=session['id']
        other=(await client.post('/api/chat/projects/p/bugs/'+second['id']+'/conversation',json={})).json();assert other['case_binding']!=session['case_binding']
        assert (await client.get('/api/chat/projects/p/bugs/bug_'+'f'*32+'/conversation')).status_code==404
    context=view_snapshot(service,session,CaseView())
    assert context['entity_type']=='bug' and context['bug']['example'] is True and context['bug']['status']=='unverified'
    assert context['bug']['actual']=='Assumed missing byte; not reproduced'
    with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='runs'))
    with pytest.raises(HTTPException):view_snapshot(service,session,CaseView(tab='source',binding_id='foreign'))
    with pytest.raises(ValueError):execute(root,dict(operation='update',target_id=first['id'],expected_revision=Bugs(root).read().revision,values=dict(status='confirmed',evidence='Demo is not evidence')))
    assert ChatService(tmp_path/'state').sessions[session['id']]['case_binding']==session['case_binding']


def test_delete_bug_requires_current_revision_and_keeps_other_records(tmp_path):
    """Remove only the selected Bug via its stable ID and the current revision."""
    first = execute(tmp_path, dict(operation='create', name='First', expected_revision=0))
    second = execute(tmp_path, dict(operation='create', name='Second', expected_revision=1))
    request = dict(operation='delete', target_id=first['id'], expected_revision=1)
    with pytest.raises(ValueError):
        execute(tmp_path, request)
    assert [row.id for row in Bugs(tmp_path).read().targets] == [first['id'], second['id']]
    request['expected_revision'] = 2
    assert execute(tmp_path, request) == {'deleted': [first['id']], 'revision': 3}
    assert [row.id for row in Bugs(tmp_path).read().targets] == [second['id']]


def test_bug_sources_are_anchored_without_rewriting_rtl(tmp_path):
    """Save trigger and impact, resolve source shifts, and reject stale ranges."""
    path = tmp_path / 'dut.sv'
    original = 'module dut;\n  wire signal;\nendmodule\n'
    path.write_text(original)
    bug = execute(tmp_path, dict(operation='create', name='Signal issue', expected_revision=0, values={
        'condition': 'After reset', 'impact': 'Signal can stay low',
        'source_refs': [{'file': 'dut.sv', 'line': 2}],
    }))
    ref_id = bug['source_refs'][0]['id']
    store = Bugs(tmp_path)
    assert bug['condition'] == 'After reset'
    assert bug['impact'] == 'Signal can stay low'
    assert path.read_text() == original
    assert store.source(ref_id)['line'] == 2
    path.write_text('// @flowcanvas id=bind_' + 'a' * 32 + '\n' + original)
    assert store.source(ref_id)['line'] == 3
    assert store.status()['reports'][bug['id']]['bindings'][0]['line'] == 3
    path.write_text(original.replace('signal', 'changed'))
    assert store.status()['reports'][bug['id']]['bindings'][0]['status'] == 'changed'
    with pytest.raises(ValueError):
        store.source(ref_id)
    with pytest.raises(ValueError):
        execute(tmp_path, dict(operation='update', target_id=bug['id'], expected_revision=1,
                              values={'source_refs': [{'file': 'dut.sv', 'line': 99}]}))


def test_bug_batch_lifecycle_is_owned_atomic_and_resumable(tmp_path):
    """Use the shared card protocol and preserve RTL through assignment and deletion."""
    path = tmp_path / 'dut.sv'
    source = 'module dut;\n  wire signal;\nendmodule\n'
    path.write_text(source)
    store = Bugs(tmp_path)

    def operation(kind, owner='origin', **values):
        """Call a shared Bug operation with the current test revision."""
        return execute(tmp_path, dict(operation=kind, expected_revision=store.read().revision, **values), owner)

    identities = operation('apply', count=2)['ids']
    assert len(identities) == 2 and all(identity.startswith('bug_') for identity in identities)
    assert all(row.lifecycle == 'draft' for row in store.read().targets)
    operation('begin_modify', ids=identities)
    first, second = identities
    assert all(row.lifecycle == 'creating' and row.operation_status == 'running' for row in store.read().targets)
    good = dict(id=first, name='Signal issue', description='Output can stay low',
                bindings=[dict(file='dut.sv', line=2, line_count=1)])
    other = dict(id=second, name='Timing issue', description='Timing evidence pending')
    with pytest.raises(ValueError):
        operation('update', cards=[good, {**other, 'bindings': [{'file': 'missing.sv', 'line': 1, 'line_count': 1}]}])
    assert store.read().targets[0].name == '待完善'
    operation('update', cards=[good, other])
    store.reconcile_operations({})
    assert all(row.operation_status == 'paused' for row in store.read().targets)
    with pytest.raises(ValueError):
        operation('begin_modify', owner='another', ids=identities)
    operation('begin_modify', ids=identities)
    operation('end_modify', ids=identities)
    ref = store.read().targets[0].source_refs[0]
    assert operation('inspect', ids=[first], binding_id=ref.id)['source']['line'] == 2
    operation('begin_modify', ids=identities, action='delete')
    assert len(store.read().targets) == 2 and all(row.lifecycle == 'deleting' for row in store.read().targets)
    assert operation('end_modify', ids=identities)['deleted'] == sorted(identities)
    assert not store.read().targets and path.read_text() == source
