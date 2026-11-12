"""Platform-owned VCS database discovery, URG conversion and source-checked mappings."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal

from .coverage_schema import validate_report
from .urg_code import read_code
from .urg_native import read_groups


def project_file(root, name):
    """Resolve a declared project file without escaping the workspace."""
    path=(Path(root)/name).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Coverage input escapes project: '+str(name))
    return path


def configuration(root):
    """Read fixed-location metadata; simulator commands are never project-defined."""
    folder=Path(root)/'.flowcanvas/coverage'
    config=json.loads((folder/'config.json').read_text())
    if config.get('provider')!='vcs':raise ValueError('Unsupported native coverage provider')
    return config


def build_identity(root):
    """Hash declared compiled artifacts and design metadata, excluding runtime hit data."""
    root=Path(root).resolve();folder=root/'.flowcanvas/coverage'
    manifest=json.loads((folder/'build.json').read_text())
    inputs=manifest.get('inputs')
    if not isinstance(inputs,list) or not inputs:raise ValueError('Coverage build.json needs compiled inputs')
    digest=hashlib.sha256()
    for name in sorted(inputs):
        path=project_file(root,name)
        files=sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
        if not files:raise ValueError('Coverage build input is empty: '+name)
        for file in files:
            file=project_file(root,file)
            if 'testdata' in file.parts:raise ValueError('Runtime data cannot define build identity')
            digest.update(str(file.relative_to(root)).encode()+b'\0')
            with file.open('rb') as stream:
                while chunk:=stream.read(1024*1024):digest.update(chunk)
            digest.update(b'\0')
    return digest.hexdigest()


def fingerprint(root):
    """Observe native database and metadata changes without reading large counter files."""
    root=Path(root);folder=root/'.flowcanvas/coverage';entries=[('reader_version',2)]
    for path in [folder/'config.json',folder/'build.json',folder/'targets.json',root/'.flowcanvas/project.json']:
        if path.is_file():
            stat=path.stat();entries.append((str(path),stat.st_mtime_ns,stat.st_size))
    manifest=folder/'build.json'
    if manifest.exists():
        for name in json.loads(manifest.read_text()).get('inputs',[]):
            path=project_file(root,name)
            for file in sorted(path.rglob('*')) if path.is_dir() else [path]:
                if file.is_file():
                    stat=file.stat();entries.append((str(file),stat.st_mtime_ns,stat.st_size))
    database=folder/'database/coverage.vdb'
    tests=database/'snps/coverage/db/testdata'
    if not tests.is_dir() or not any(tests.iterdir()):return None
    for path in sorted(database.rglob('*')):
        if path.is_file():
            stat=path.stat();entries.append((str(path.relative_to(folder)),stat.st_mtime_ns,stat.st_size))
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def map_functional(root, groups):
    """Use explicit native selectors and compiled-source fingerprints, never title guesses."""
    root=Path(root);mapping=root/'.flowcanvas/coverage/targets.json'
    if not mapping.exists():return dict(observations=[]),[]
    entries=json.loads(mapping.read_text());observations=[];warnings=[]
    for item in entries:
        selector=item['selector']
        matches=[g for g in groups if g['type']==selector['type'] and
                 (not selector.get('instance') or g['instance']==selector['instance'])]
        if len(matches)!=1:raise ValueError('Coverage mapping must select exactly one native instance: '+item['target_id'])
        group=matches[0]
        raw=project_file(root,group['source']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:
            raise ValueError('Compiled coverage source differs from mapping: '+item['file'])
        points=[p for p in group['points'] if not selector.get('point') or p['name']==selector['point']]
        if not points:raise ValueError('Mapped native coverpoint missing: '+item['target_id'])
        if 'bin' in selector:
            name=selector['bin']
            bins=[b for p in points for b in p['reported_bins'] if b['name']==name or b['name'].startswith(name+'[')]
            if not bins:
                if selector.get('excluded'):
                    warnings.append('Excluded native bin: '+item['target_id']);continue
                raise ValueError('Mapped native bin missing: '+item['target_id'])
            covered=sum(b['hits']>=b['goal'] for b in bins);total=len(bins)
        else:
            covered=sum(p['covered'] for p in points);total=sum(p['total'] for p in points)
            bins=[dict(b,name=p['name']+'/'+b['name']) for p in points for b in p['bins']]
            if len(bins)!=total:bins=[]
        observations.append(dict(target_id=item['target_id'],binding_fingerprints=item['binding_fingerprints'],
                                 covered=covered,total=total,bins=bins))
    return dict(observations=observations),warnings


async def extract(root, database, output, build_id):
    """Run native URG and convert one stable database snapshot into the shared schema."""
    root=Path(root).resolve();output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    config=configuration(root);folder=root/'.flowcanvas/coverage'
    executable=config.get('urg_executable') or shutil.which('urg')
    if not executable:raise ValueError('URG is unavailable; configure urg_executable or put urg on PATH')
    compile_db=folder/'database/compile.vdb';databases=[str(database)]
    if compile_db.is_dir():
        # Reuse only compilation metadata, never old runtime tests in a build database.
        metadata=output.parent/'compile.vdb'
        if metadata.exists():shutil.rmtree(metadata)
        await asyncio.to_thread(shutil.copytree,compile_db,metadata,
                                ignore=lambda directory,names: ['testdata'] if 'testdata' in names else [])
        databases.insert(0,str(metadata))
    environment=dict(os.environ)
    tool=Path(executable).resolve()
    if tool.parent.name=='bin' and tool.name=='urg':
        environment.setdefault('VCS_HOME',str(tool.parent.parent))
    report_dir=output.parent/'urg-report'
    with (output.parent/'urg.log').open('wb') as log:
        process=await asyncio.create_subprocess_exec(executable,'-full64','-dir',*databases,
            '-metric','line+tgl+branch+cond+fsm+assert+group','-format','text','-show','ratios','-group','show_bin_values',
            '-group','lrm_bin_name','-report',str(report_dir),cwd=root,env=environment,
            stdout=log,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
        try:
            await asyncio.wait_for(process.wait(),180)
        finally:
            if process.returncode is None:
                os.killpg(process.pid,signal.SIGKILL);await process.wait()
    if process.returncode:raise ValueError('URG extraction failed; see '+str(output.parent/'urg.log'))
    project=json.loads((root/'.flowcanvas/project.json').read_text())
    rtl=project['rtl'];text=(report_dir/'modinfo.txt').read_text()
    scope=config.get('dut_instance')
    if not scope:raise ValueError('Configure the native DUT instance in coverage/config.json')
    instances,metrics=read_code(text,root,scope,rtl['top'],rtl['files'])
    group_file=report_dir/'grpinfo.txt'
    functional,warnings=map_functional(root,read_groups(group_file.read_text())) if group_file.exists() else (dict(observations=[]),[])
    if (folder/'targets.json').exists() and not functional['observations']:
        raise ValueError('Native functional coverage mapping produced no observations')
    report=validate_report(dict(schema_version=1,build_id=build_id,provider='vcs',instances=instances,
                               code_metrics=metrics,functional=functional,warnings=warnings))
    if await asyncio.to_thread(build_identity,root)!=build_id:
        raise ValueError('Compiled build changed during native coverage extraction')
    from .targets import atomic
    atomic(output,report)
    return report


def writer_active(root):
    """Recognize local VCS processes still writing the agreed database, including external runs."""
    database=str(Path(root).resolve()/'.flowcanvas/coverage/database/coverage.vdb')
    proc=Path('/proc')
    if not proc.is_dir():return False
    for path in proc.iterdir():
        if not path.name.isdigit():continue
        try:
            args=(path/'cmdline').read_bytes().decode(errors='replace').split('\0')
            for i,arg in enumerate(args[:-1]):
                if arg=='-cm_dir':
                    candidate=Path(args[i+1])
                    if not candidate.is_absolute():candidate=(path/'cwd').resolve()/candidate
                    if str(candidate.resolve())==database:return True
        except (OSError,RuntimeError):continue
    return False


def metadata_identity(root):
    """Bind smoke receipts to native extraction metadata and the executed compilation."""
    folder=Path(root)/'.flowcanvas/coverage';digest=hashlib.sha256(build_identity(root).encode())
    for name in ('config.json','build.json','targets.json'):
        path=folder/name
        if path.exists():digest.update(name.encode()+path.read_bytes())
    return digest.hexdigest()
