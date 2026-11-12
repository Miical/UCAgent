"""Fixture-only helpers selecting explicit ranges for target tests."""
from flowcanvas.testcase_syntax import inventory
from flowcanvas.targets import execute


def inspect_fixture(store,files):
    """Locate nodes in test fixtures without involving the target production API."""
    return [{'nodes':inventory(store.file(f))['nodes']} for f in files]


def bind_target(store,identity,values,revision):
    """Bind a fixture range through the public lifecycle."""
    node=next(n for n in inventory(store.file(values['file']))['nodes'] if n['start']==values['start'])
    def call(op,**kwargs):
        """Apply one owned fixture transaction."""
        return execute(store.root,dict(operation=op,expected_revision=store.read().revision,**kwargs),'fixture')
    call('begin_modify',ids=[identity])
    row=next(t for t in store.read().targets if t.id==identity)
    call('update',cards=[dict(id=identity,description=row.description or 'Fixture source range',bindings=[
        dict(file=values['file'],line=node['line'],line_count=node['end_line']-node['line']+1,role=values.get('role','definition'))])])
    call('end_modify',ids=[identity])
    return store.status()


def reserve_case(store,name,values,revision,owner):
    """Create a case fixture through the current five-operation API."""
    from flowcanvas.testcases import execute
    values.setdefault('runner', {'command':'python -m pytest {case}', 'description':'Fixture pytest entry'})
    result=execute(store.root,dict(operation='apply',count=1,expected_revision=revision),owner)
    identity=result['ids'][0]
    execute(store.root,dict(operation='begin_modify',ids=[identity],expected_revision=store.read().revision),owner)
    execute(store.root,dict(operation='update',cards=[dict(id=identity,name=name,**values)],expected_revision=store.read().revision),owner)
    return next(t.model_dump() for t in store.read().targets if t.id==identity)


def bind_case(store,identity,file,name,owner='fixture',finish=True):
    """Select a complete fixture function and persist an explicit line binding."""
    from flowcanvas.testcase_syntax import inventory
    from flowcanvas.testcases import execute
    node=next(n for n in inventory(store.file(file))['nodes'] if n['name']==name)
    execute(store.root,dict(operation='update',expected_revision=store.read().revision,cards=[dict(id=identity,
        bindings=[dict(file=file,line=node['line'],line_count=node['end_line']-node['line']+1)])]),owner)
    if finish:
        execute(store.root,dict(operation='end_modify',expected_revision=store.read().revision,ids=[identity]),owner)
    return store.status()


def register_case(store,file,name,title,description):
    """Register one source fixture with readable case metadata."""
    row=reserve_case(store,name,dict(title=title,description=description),store.read().revision,'fixture')
    return bind_case(store,row['id'],file,name)


def set_case_runners(store, runner):
    """Assign a command to every case in a local test fixture."""
    from flowcanvas.testcases import execute
    ids=[t.id for t in store.read().targets]
    if not ids:
        return
    for operation,payload in [('begin_modify',{'ids':ids}),
                              ('update',{'cards':[{'id':i,'runner':runner} for i in ids]}),
                              ('end_modify',{'ids':ids})]:
        execute(store.root,dict(operation=operation,expected_revision=store.read().revision,**payload),'fixture')
