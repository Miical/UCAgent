"""Source-mapped native line and toggle extraction without testbench pollution."""
import pytest
from flowcanvas.urg_code import read_code


def test_native_line_ranges_toggle_directions_and_panel_paths(tmp_path):
    """Use native counts and bit ranges, excluding unreachable directions and generated modules."""
    source=tmp_path/'Top.sv';source.write_text('module Top(input [1:0] a);\nassign y=a;\nendmodule\n')
    text=f'''Module : Top
Source File(s) :
{source}
Module self-instances :
SCORE LINE TOGGLE NAME
75.00 50.00 1/2 100.00 4/4 tb.dut
Line Coverage for Module : Top
2 1/2 assign y=a;
Toggle Coverage for Module : Top
Total Bits 4 4 100.00
Port Details
Toggle Toggle 1->0 Toggle 0->1 Direction
a[1:0] Yes Yes Yes INPUT
constant Unreachable Unreachable Unreachable INPUT
Module Instance : tb.dut
Instance :
'''
    rows,metrics=read_code(text,tmp_path,'tb.dut','Top',['Top.sv'])
    assert rows[0]['path']=='Top'
    assert rows[0]['covered']==1 and rows[0]['total']==2
    assert rows[0]['files'][0]['partial_lines']==[2]
    assert rows[0]['files'][0]['path']=='Top.sv'
    assert metrics['toggle']['dut']['total']==4
    assert metrics['toggle']['dut']['covered']==4
    assert (tmp_path/'.flowcanvas/coverage/sources').is_dir()
    with pytest.raises(ValueError,match='native totals'):
        read_code(text.replace('Total Bits 4 4','Total Bits 8 8'),tmp_path,'tb.dut','Top',['Top.sv'])
    source.write_text('module Changed;endmodule')
    # Simulate a distinct compiled source snapshot to detect that project RTL no longer matches.
    compiled=tmp_path/'compiled.sv';compiled.write_text('module Top(input [1:0] a);\nassign y=a;\nendmodule\n')
    with pytest.raises(ValueError,match='source-bound'):
        read_code(text.replace(str(source),str(compiled)),tmp_path,'tb.dut','Top',['Top.sv'])


@pytest.mark.parametrize('reverse',[False,True])
def test_compressed_other_bits_exclude_explicit_ranges_and_unreachable(tmp_path,reverse):
    """Expand multidimensional remainder bits without double counting listed or unreachable bits."""
    source=tmp_path/'Top.sv';source.write_text('module Top;\nwire [1:0][3:0] data;\nendmodule\n')
    rows=['data[0][1:0] Yes Yes Yes', 'data[1][0] No No Yes',
          'data[1][3] Unreachable Unreachable Unreachable',
          'Other bits of data[1:0][3:0] No No No']
    if reverse:rows.reverse()
    text=f"""Module : Top
Source File(s) :
{source}
Module self-instances :
SCORE LINE TOGGLE NAME
50.0 1/1 5/14 tb.dut
Line Coverage for Module : Top
2 1/1 wire data;
Toggle Coverage for Module : Top
Total Bits 14 5 35.71
"""+'\n'.join(rows)
    _,metrics=read_code(text,tmp_path,'tb.dut','Top',['Top.sv'])
    summary=metrics['toggle']['dut']
    assert (summary['covered'],summary['total'])==(5,14)
    items={a['signal']:a['hits'] for a in summary['items']}
    assert items['data[0][2]:1->0']==0
    assert items['data[1][0]:0->1']==1
    assert not any('data[1][3]' in name for name in items)
    assert len({a['id'] for a in summary['items']})==14
    with pytest.raises(ValueError,match='Overlapping'):
        read_code(text+'\ndata[0][0] Yes Yes Yes',tmp_path,'tb.dut','Top',['Top.sv'])
    with pytest.raises(ValueError,match='native totals'):
        read_code(text.replace('Total Bits 14 5','Total Bits 16 5'),tmp_path,'tb.dut','Top',['Top.sv'])
