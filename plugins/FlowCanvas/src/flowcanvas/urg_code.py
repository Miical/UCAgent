"""Extract source-bound VCS code metrics and assertion observations from URG text."""
import hashlib
import itertools
from pathlib import Path
import re
from .urg_metrics import section, read_metrics


def read_code(text, project, dut_instance, top_module, source_files):
    """Map explicit DUT scope to panel paths and verify native totals before publication."""
    root=Path(project).resolve();sources={}
    for name in source_files:
        path=(root/name).resolve();raw=path.read_bytes()
        sources.setdefault(hashlib.sha256(raw).hexdigest(),[]).append((name,raw))
    instances=[];atoms=[];extra={}
    sections=re.split(r'^Module : (\S+)\s*$',text,flags=re.M)
    for i in range(1,len(sections),2):
        module,body=sections[i:i+2]
        source=re.search(r'Source File\(s\) :\s*\n\s*(\S+)',body)
        if not source:continue
        raw=Path(source[1]).read_bytes();sha=hashlib.sha256(raw).hexdigest()
        matches=sources.get(sha,[])
        if not matches:continue  # Only explicit project RTL, not verification interfaces.
        if len(matches)!=1:raise ValueError('Ambiguous RTL source snapshot: '+source[1])
        file,_=matches[0];cache=root/'.flowcanvas/coverage/sources'/(sha+Path(file).suffix)
        cache.parent.mkdir(parents=True,exist_ok=True);cache.write_bytes(raw)
        chunks=re.split(r'^Module Instance : (\S+)\s*$',body,flags=re.M)
        header=chunks[0]
        table=header.split('Module self-instances :',1)[-1].split('Line Coverage',1)[0].split('Toggle Coverage',1)[0]
        names=re.findall(r'^.*\s(\S+)\s*$',table,re.M)
        scoped=[n for n in names if n==dut_instance or n.startswith(dut_instance+'.')]
        for native in scoped:
            path=top_module+native[len(dut_instance):]
            detail=next((chunks[k+1] for k in range(1,len(chunks),2) if chunks[k]==native),'')
            # Single-instance URG reports place detailed observations in the module header.
            linebody=detail if 'Line Coverage for Instance' in detail else header if len(scoped)==1 else ''
            linebody=section(linebody,'Line')
            for kind, summary in read_metrics(header,detail,len(scoped)==1,native,path,module,file,sha,raw).items():
                dest=extra.setdefault(kind,dict(covered=0,total=0,items=[]))
                dest['covered']+=summary['covered'];dest['total']+=summary['total'];dest['items'].extend(summary['items'])
                if 'states' in summary:dest.setdefault('states',[]).extend(summary['states'])
            linepairs={}
            for line,hit,total in re.findall(r'^\s*(\d+)\s+(\d+)/(\d+)\s+',linebody,re.M):
                line=int(line);hit=int(hit);total=int(total)
                old=linepairs.get(line,(0,0));linepairs[line]=(old[0]+hit,old[1]+total)
            if linepairs:
                total=sum(v[1] for v in linepairs.values());covered=sum(v[0] for v in linepairs.values())
                lines=dict(path=file,sha256=sha,line_hits={str(k):v[0] for k,v in linepairs.items()},
                    covered_lines=sorted(k for k,(h,t) in linepairs.items() if h==t),
                    uncovered_lines=sorted(k for k,(h,t) in linepairs.items() if h==0),
                    partial_lines=sorted(k for k,(h,t) in linepairs.items() if 0<h<t))
                instances.append(dict(path=path,simulation_path=native,module=module,covered=covered,total=total,files=[lines]))
            toggle=detail if 'Toggle Coverage for Instance' in detail else header if len(scoped)==1 else ''
            if 'Toggle Coverage for' not in toggle:continue
            toggle=section(toggle,'Toggle')
            expected=re.search(r'^Total Bits\s+(\d+)\s+(\d+)\s',toggle,re.M)
            current=[];explicit={};rows=[]
            for other,signal,all_hit,fall,rise in re.findall(r'^\s*(Other bits of\s+)?(\S+)\s+(Yes|No|Unreachable)\s+(Yes|No|Unreachable)\s+(Yes|No|Unreachable)(?:\s|$)',toggle,re.M):
                base=signal.split('[')[0];dimensions=[]
                for lo,hi in re.findall(r'\[(\d+)(?::(\d+))?\]',signal):
                    lo=int(lo);hi=int(hi) if hi else lo;dimensions.append(range(min(lo,hi),max(lo,hi)+1))
                bits=set(itertools.product(*dimensions)) if dimensions else {()}
                # URG's compressed remainder excludes every explicitly listed bit, including unreachable bits.
                if not other:
                    known=explicit.setdefault(base,set())
                    if known & bits:raise ValueError('Overlapping URG toggle bit details: '+native+':'+signal)
                    known.update(bits)
                rows.append((bool(other),base,bits,fall,rise))
            seen=set()
            for other,base,bits,fall,rise in rows:
                if other:bits=bits-explicit.get(base,set())
                declaration=re.search(r'\b'+re.escape(base)+r'\b',raw.decode(errors='replace'))
                line=raw[:declaration.start()].count(b'\n')+1 if declaration else 1
                for bit in sorted(bits):
                    for direction,value in [('1->0',fall),('0->1',rise)]:
                        label=base+''.join('['+str(b)+']' for b in bit)+':'+direction
                        if label in seen:raise ValueError('Overlapping URG toggle bit details: '+native+':'+label)
                        seen.add(label)
                        if value=='Unreachable':continue
                        key=hashlib.sha256((native+'\0'+label).encode()).hexdigest()
                        current.append(dict(id=key,kind='toggle',scope='dut',file=file,sha256=sha,
                            line=line,hits=int(value=='Yes'),hierarchy=path,module=module,signal=label))
            if expected and (len(current)!=int(expected[1]) or sum(a['hits'] for a in current)!=int(expected[2])):
                raise ValueError('URG toggle bit details disagree with native totals: '+native+
                                 f" (details {sum(a['hits'] for a in current)}/{len(current)}, native {expected[2]}/{expected[1]})")
            atoms.extend(current)
    if not instances:raise ValueError('No source-bound DUT line observations')
    metrics={'toggle':{'dut':dict(covered=sum(a['hits']>0 for a in atoms),total=len(atoms),items=atoms)}} if atoms else {}
    metrics.update({kind:{'dut':summary} for kind,summary in extra.items()})
    return instances,metrics
