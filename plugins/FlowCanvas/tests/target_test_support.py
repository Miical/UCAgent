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
