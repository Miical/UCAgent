"""Module source views resolve declarations rather than parent instantiation sites."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from flowcanvas import rtl_viewer
from flowcanvas.module_details import module_record


@pytest.mark.parametrize('newline',[b'\n',b'\r\n'])
def test_child_definition_and_instantiation_are_distinct(tmp_path,monkeypatch,newline):
    """Resolve full child declarations, keep parent locations and reuse enriched cached data."""
    root=tmp_path/'project';root.mkdir();metadata=root/'.flowcanvas';metadata.mkdir()
    (root/'top.sv').write_bytes(b'module top;\n  child u_child();\nendmodule\n'.replace(b'\n',newline))
    (root/'child.sv').write_text('module child;\n  wire data;\n  // endmodule in a comment is not a declaration boundary\nendmodule\n')
    (root/'rtl.f').write_text('top.sv\nchild.sv\n')
    (metadata/'project.json').write_text(json.dumps(dict(rtl=dict(top='top',filelist='rtl.f'))))
    project=dict(id='p',path=str(root),name='fixture');service=SimpleNamespace(root=tmp_path/'state',projects=SimpleNamespace(state={'recent':[project]}))
    calls=[]
    def extract(command,**kwargs):
        """Model the upstream format that uses the parent's source for child instances."""
        calls.append(command)
        model=dict(top='top',nodes=[dict(id='top',module='top',source=dict(file=str(root/'top.sv'),start_line=1,end_line=3)),dict(id='top.u_child',module='child',source=dict(file=str(root/'top.sv'),start_line=4,end_line=6))],edges=[{'from':'top','to':'top.u_child'}])
        Path(command[command.index('--output')+1]).write_text(json.dumps(model))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(rtl_viewer,'version',lambda _: 'fixture')
    monkeypatch.setattr(rtl_viewer.shutil,'which',lambda *args,**kwargs:'/tools/verible')
    monkeypatch.setattr(rtl_viewer.subprocess,'run',extract)
    rtl_viewer.routes(service)
    first=service.rtl_view('p');cached=service.rtl_view('p')
    assert cached==first and len(calls)==1
    node=first['nodes'][1]
    assert node['source']['file']==str(root/'child.sv')
    assert node['source']['start_line']==1 and node['source']['end_line']==4
    assert node['instance_source']['file']==str(root/'top.sv')
    _,card,source=module_record(service,'p',instance='top.u_child')
    assert card.name=='child' and source['file']=='child.sv'
    assert 'wire data' in source['content'] and 'u_child' not in source['content']
    assert {key:source['instantiation'][key] for key in ('file','line','end_line')}==dict(file='top.sv',line=2,end_line=2)
    assert source['instantiation']['display_line']==1
    assert 'module top;' in source['instantiation']['content'] and 'child u_child();' in source['instantiation']['content']
    # Multiple declarations cannot be silently resolved to either file or to the parent.
    (root/'duplicate.sv').write_text('module child;\nendmodule\n')
    (root/'rtl.f').write_text('top.sv\nchild.sv\nduplicate.sv\n')
    assert service.rtl_view('p')['nodes'][1]['source'] is None
    with pytest.raises(HTTPException):module_record(service,'p',instance='top.u_child')


@pytest.mark.parametrize('newline',[b'\n',b'\r\n'])
def test_real_parser_keeps_port_and_parameter_expressions(tmp_path,newline):
    """Run the actual frontend on LF/CRLF input, retaining expressions and original project files."""
    import shutil
    if not shutil.which('verible-verilog-syntax'):pytest.skip('Verible is not installed')
    root=tmp_path/'project';root.mkdir();metadata=root/'.flowcanvas';metadata.mkdir()
    prefix='// Multi-byte comment: '+chr(0x03b1)+'\n'
    top=prefix+'module top(input logic clk, input logic [7:0] a, output logic [7:0] q);\n'+('// Padding before instance\n'*20)+"  child #(.WIDTH(8)) u_child (\n    .clk(clk),\n    .d(a ^ 8'h5a),\n    .q(q)\n  );\nendmodule\n"
    child='module child #(parameter WIDTH=8)(input logic clk, input logic [WIDTH-1:0] d, output logic [WIDTH-1:0] q);\n  assign q=d;\nendmodule\n'
    originals={name:text.encode().replace(b'\n',newline) for name,text in [('top.sv',top),('child.sv',child)]}
    for name,data in originals.items():(root/name).write_bytes(data)
    (root/'rtl.f').write_text('top.sv\nchild.sv\n');(metadata/'project.json').write_text(json.dumps(dict(rtl=dict(top='top',filelist='rtl.f'))))
    project=dict(id='p',path=str(root),name='fixture');service=SimpleNamespace(root=tmp_path/'state',projects=SimpleNamespace(state={'recent':[project]}))
    rtl_viewer.routes(service);model=service.rtl_view('p');node=next(node for node in model['nodes'] if node['id']=='top.u_child')
    ports={port['name']:port.get('expr') for port in node['ports']}
    assert ports['clk']=='clk' and ports['q']=='q'
    assert ''.join(ports['d'].split())=="a^8'h5a"
    assert str(node['parameters']['WIDTH'])=='8'
    assert node['source']['file']==str(root/'child.sv')
    assert node['instance_source']['file']==str(root/'top.sv')
    for name,data in originals.items():assert (root/name).read_bytes()==data
