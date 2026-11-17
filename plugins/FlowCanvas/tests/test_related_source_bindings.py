"""Related case fragments and configuration bindings retain real source semantics."""
import pytest
from flowcanvas.testcases import Cases, execute


@pytest.mark.parametrize('newline',['\n','\r\n'])
def test_related_fragments_and_configuration_roundtrip(tmp_path,newline):
    """Retain every role, exact script line endings and valid ranges across separate files."""
    contents={'case.sv':'class bus_test;\nendclass\n','stimulus.sv':'task send();\nendtask\n',
              'check.sv':'assert property (req |-> ack);\n','run.sh':'#!/bin/bash\nexec simv "$@"\n'}
    for name,text in contents.items():(tmp_path/name).write_bytes(text.replace('\n',newline).encode())
    def call(operation,**values):
        """Apply changes through the owned public transaction."""
        return execute(tmp_path,dict(operation=operation,expected_revision=Cases(tmp_path).read().revision,**values),'test')
    identity=call('apply',count=1)['ids'][0];call('begin_modify',ids=[identity])
    call('update',cards=[dict(id=identity,name='bus_test',title='Bus transaction',description='Send and check a transaction',runner=dict(command='bash run.sh {case} {wave_dir}',description='Existing simulator runner'),bindings=[dict(file=name,line=2 if name=='run.sh' else 1,line_count=1,role=role) for name,role in [('case.sv','definition'),('stimulus.sv','stimulus'),('check.sv','check'),('run.sh','configuration')]])])
    call('end_modify',ids=[identity])
    state=call('inspect');assert state['reports'][identity]['status']=='bound'
    assert {b['role'] for b in state['reports'][identity]['bindings']}=={'definition','stimulus','check','configuration'}
    script=(tmp_path/'run.sh').read_bytes();assert script.startswith(('#!/bin/bash'+newline).encode())
    if newline=='\r\n':assert b'\n' not in script.replace(b'\r\n',b'')
    for binding in state['reports'][identity]['bindings']:
        source=call('inspect',binding_id=binding['id'])['source'];assert source['content'].strip()
    (tmp_path/'check.sv').write_text('assert property (req |-> 1);\n')
    assert call('inspect')['reports'][identity]['status']!='bound'


def test_shebang_cannot_be_displaced_by_a_binding(tmp_path):
    """Reject a marker above an executable script's shebang without modifying either file or card."""
    script=tmp_path/'run.sh';script.write_text('#!/bin/bash\nexec simv\n')
    def call(operation,**values):
        """Use one owner and the newest revision for every change."""
        return execute(tmp_path,dict(operation=operation,expected_revision=Cases(tmp_path).read().revision,**values),'test')
    identity=call('apply',count=1)['ids'][0];call('begin_modify',ids=[identity]);before=script.read_bytes()
    with pytest.raises(ValueError,match='shebang'):
        call('update',cards=[dict(id=identity,bindings=[dict(file='run.sh',line=1,line_count=2,role='configuration')])])
    assert script.read_bytes()==before and not Cases(tmp_path).read().targets[0].bindings
