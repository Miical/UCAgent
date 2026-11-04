"""Pinned uvm-cache / Redmi VCS W-2024.09-SP1 integration."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from .targets import atomic

REMOTE = r'''
import hashlib,json,pathlib,shutil,subprocess,sys
p=json.loads(sys.argv[1]);root=pathlib.Path('/home/miical/eda/projects/uvm-cache');build=root/p.get('build_dir','build')
env='source /home/miical/eda/synopsys-2024/env.sh; '
def shell(command,cwd):
 return subprocess.run(['/bin/bash','-c',env+command],cwd=cwd)
def identity():
 h=hashlib.sha256()
 files=[build/'simv',build/'Cache.v',build/'filelist.f']+sorted((build/'covdir.vdb/snps/coverage/db/design').rglob('*'))
 for f in files:
  if f.is_file():h.update(str(f.relative_to(build)).encode());h.update(f.read_bytes())
 return h.hexdigest()
if p['op']=='identity':print(identity())
elif p['op']=='run':
 run=root/'.flowcanvas/runs'/p['id'];run.mkdir(parents=True,exist_ok=False)
 before=identity();(run/'build-id').write_text(before)
 shutil.copy2(build/'Cache.v',run/'Cache.v')
 if (build/'src/components/cache_bus_cov.sv').is_file():shutil.copy2(build/'src/components/cache_bus_cov.sv',run/'cache_bus_cov.sv')
 shutil.copytree(build/'covdir.vdb',run/'coverage.vdb',ignore=lambda d,n:['testdata'] if pathlib.Path(d).name=='db' else [])
 (run/'simv').symlink_to(build/'simv');(run/'simv.daidir').symlink_to(build/'simv.daidir')
 cmd='./simv -no_save +UVM_VERBOSITY=UVM_LOW +UVM_TESTNAME='+p['case']+' -cm line+cond+fsm+branch+tgl -cm_dir '+str(run/'coverage.vdb')+' -cm_name '+p['id']
 rc=shell(cmd,run).returncode
 if identity()!=before:raise RuntimeError('Build changed during simulation')
 (run/'finished.json').write_text(json.dumps({'exit_code':rc,'build_id':before}))
 sys.exit(rc)
elif p['op']=='extract':
 run=root/'.flowcanvas/runs'/p['id'];result=json.loads((run/'finished.json').read_text())
 if not list((run/'coverage.vdb/snps/coverage/db/testdata').glob('*')):raise RuntimeError('No runtime coverage data')
 print(json.dumps(dict(result,artifact=str(run/'coverage.vdb'),rtl_sha256=hashlib.sha256((run/'Cache.v').read_bytes()).hexdigest(),functional_source=(run/'cache_bus_cov.sv').read_text() if (run/'cache_bus_cov.sv').is_file() else '')))
elif p['op']=='merge':
 out=root/'.flowcanvas/coverage'/p['id'];out.mkdir(parents=True,exist_ok=False)
 inputs=p['inputs']
 if not inputs or any(not str(x).startswith(str(root/'.flowcanvas/runs')+'/') for x in inputs):raise RuntimeError('Invalid input database')
 import shlex
 cmd='urg -dir '+' '.join(shlex.quote(x) for x in inputs)+' -metric line+group -format text -show ratios -group instcov_for_score -group lrm_bin_name -dbname '+shlex.quote(str(out/'merged'))+' -report '+shlex.quote(str(out/'report'))
 with (out/'urg.log').open('w') as log:
  proc=subprocess.run(['/bin/bash','-c',env+cmd],cwd=out,stdout=log,stderr=subprocess.STDOUT)
 if proc.returncode:raise RuntimeError('URG merge failed; see '+str(out/'urg.log'))
 print(json.dumps({'line':(out/'report/modinfo.txt').read_text(),'group':(out/'report/grpinfo.txt').read_text() if (out/'report/grpinfo.txt').exists() else ''}))
'''

def remote(payload,capture=True):
    command=shlex.join(['python3','-c',REMOTE,json.dumps(payload)])
    return subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','redmi',command],capture_output=capture,text=True,check=capture)


def parse_report(text,build_id,rtl_hash):
    instances=[]
    sections=re.split(r'^Module : (\S+)\s*$',text,flags=re.M)
    for i in range(1,len(sections),2):
        module,body=sections[i:i+2]
        header=body.split('Module Instance :',1)[0]
        source=re.search(r'Source File\(s\) :\s*\n\s*(\S+)',header)
        table=header.split('Module self-instances :',1)[-1].split('Line Coverage',1)[0]
        rows=re.findall(r'^\s*[\d.]+\s+[\d.]+\s+(\d+)/(\d+)\s+(\S+)\s*$',table,re.M)
        if len(rows)!=1:raise ValueError('Expected one instance per module in this uvm-cache report: '+module)
        covered,total,path=rows[0]
        if path!='top_tb.cache' and not path.startswith('top_tb.cache.'):continue
        if not source or not source[1].endswith('/Cache.v'):raise ValueError('Unexpected RTL source')
        hits,misses,partial=set(),set(),set()
        for line,a,b in re.findall(r'^\s*(\d+)\s+(\d+)/(\d+)\s+',header,re.M):
            line,a,b=int(line),int(a),int(b)
            if a==b:hits.add(line)
            elif a==0:misses.add(line)
            else:partial.add(line)
        instances.append(dict(path='Cache'+path[len('top_tb.cache'):],simulation_path=path,module=module,
            covered=int(covered),total=int(total),files=[dict(path='Cache.v',covered_lines=sorted(hits),uncovered_lines=sorted(misses),partial_lines=sorted(partial))]))
    if not instances:raise ValueError('URG report has no Cache instance coverage')
    return dict(schema_version=1,build_id=build_id,rtl_sha256=rtl_hash,instances=instances)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['run','identity','extract','merge'])
    parser.add_argument('--build-dir',default='build');parser.add_argument('--run-dir');parser.add_argument('--case');parser.add_argument('--inputs-manifest');parser.add_argument('--merge-dir');a=parser.parse_args()
    if a.operation=='identity':print(remote({'op':'identity','build_dir':a.build_dir}).stdout.strip());return
    if a.operation in {'run','extract'}:
        directory=Path(a.run_dir);directory.mkdir(parents=True,exist_ok=True);identity=directory.name
        if not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('Invalid run ID')
        if a.operation=='run':
            if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',a.case or ''):raise ValueError('Invalid UVM test class')
            sys.exit(remote({'op':'run','id':identity,'case':a.case,'build_dir':a.build_dir},False).returncode)
        artifact=json.loads(remote({'op':'extract','id':identity}).stdout)
        atomic(directory/'coverage.json',artifact);return
    paths=json.loads(Path(a.inputs_manifest).read_text());items=[json.loads(Path(p).read_text()) for p in paths]
    if len({x['build_id'] for x in items})!=1 or len({x['rtl_sha256'] for x in items})!=1:raise ValueError('Incompatible builds')
    import uuid
    reports=json.loads(remote({'op':'merge','id':uuid.uuid4().hex,'inputs':[x['artifact'] for x in items]}).stdout)
    out=Path(a.merge_dir);out.mkdir(parents=True,exist_ok=True);(out/'urg-modinfo.txt').write_text(reports['line']);(out/'urg-grpinfo.txt').write_text(reports['group'])
    report=parse_report(reports['line'],items[0]['build_id'],items[0]['rtl_sha256'])
    from .functional_coverage import parse_groups,source_fingerprint
    sources=[item.get('functional_source','') for item in items]
    if len({source_fingerprint(source) for source in sources})!=1:raise ValueError('Incompatible functional coverage sources')
    report['functional']=parse_groups(reports['group'],sources[0])
    atomic(out/'coverage.json',report)

if __name__=='__main__':main()
