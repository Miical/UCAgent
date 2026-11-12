"""Shared database lifecycle and atomic coverage publication."""
import json
import shutil
from pathlib import Path
import time
import uuid
from .targets import atomic


def state(root):
    path=Path(root)/'.flowcanvas/coverage/state.json'
    if not path.exists():atomic(path,dict(epoch=uuid.uuid4().hex,build_id=None,runs=[],report=None))
    return json.loads(path.read_text())


def reset(root):
    """Delete cumulative databases/reports while retaining run history and source snapshots."""
    folder=Path(root)/'.flowcanvas/coverage'
    for child in folder.iterdir() if folder.exists() else []:
        if child.name in {'sources','config.json','build.json','targets.json'}:
            continue
        if child.name=='database' and child.is_dir() and not child.is_symlink():
            database=child/'coverage.vdb'
            if database.is_symlink() or database.is_file():database.unlink()
            elif database.is_dir():shutil.rmtree(database)
            continue  # Compile metadata must survive reset for the next simulation.
        if child.is_dir() and not child.is_symlink():shutil.rmtree(child)
        else:child.unlink()
    data=dict(epoch=uuid.uuid4().hex,build_id=None,runs=[],report=None)
    atomic(Path(root)/'.flowcanvas/coverage/state.json',data)
    return data


def render(command,context):
    import shlex
    for key,value in context.items():command=command.replace('{'+key+'}',shlex.quote(str(value)))
    return command


def context(row):
    """Resolve service-owned database paths and unique names for command templates."""
    return dict(project=row['cwd'],run_dir=row['run_dir'],case=row['entry'],
                coverage_db=row['coverage_db'],coverage_name=row['id'])


async def collect(runner,row,log):
    config=row['coverage_config'];values=context(row)
    row['coverage_status']='extracting'
    if config.get('provider')=='vcs':
        from .native_vcs import extract
        artifact=Path(row['run_dir'])/'coverage.json'
        await extract(row['cwd'],row['coverage_db'],artifact,row['build_id'])
    else:
        await runner.coverage_command(render(config['extract_command'],values),row,log)
        artifact=Path(config['artifact_path'].format(**values))
    if not artifact.is_file():raise ValueError('Coverage artifact missing')
    if row.get('mode')=='coverage_smoke':
        from .smoke_runs import validate_smoke_report
        report=validate_smoke_report(row['cwd'],json.loads(artifact.read_text()))
        if report['build_id']!=row['build_id']:
            raise ValueError('Smoke report does not match executed build')
        row.update(coverage_status='validated',coverage_artifact=str(artifact))
        return
    async with runner.lock:
        current=state(row['cwd'])
        if current['epoch']!=row['coverage_epoch']:
            row['coverage_status']='previous_batch';return
        if current.get('mode', 'per_run')=='shared' and config.get('mode','per_run')!='shared':
            raise ValueError('Coverage mode changed; clear accumulated coverage before collecting')
        if current['build_id'] not in (None,row['build_id']):raise ValueError('构建版本变化，请清空累计后重新运行')
        if row['id'] in [r['id'] for r in current['runs']]:return
        inputs=current['runs']+[dict(id=row['id'],artifact=str(artifact))]
        if config.get('mode', 'per_run') == 'shared':
            from .coverage_schema import validate_report
            report=validate_report(json.loads(artifact.read_text()),row['build_id'])
            current.update(build_id=row['build_id'],runs=inputs,report=report,updated=time.time())
            if config.get('provider')=='vcs':
                from .native_vcs import fingerprint
                current.update(database_fingerprint=fingerprint(row['cwd']),ingestion_status='ready')
                for key in ('ingestion_error','failed_fingerprint','failed_at'):current.pop(key,None)
            atomic(Path(row['cwd'])/'.flowcanvas/coverage/state.json',current)
            row['coverage_status']='merged'
            return
        merge=Path(row['cwd'])/'.flowcanvas/coverage'/current['epoch']/uuid.uuid4().hex
        merge.mkdir(parents=True)
        manifest=merge/'inputs.json';atomic(manifest,[r['artifact'] for r in inputs])
        values.update(inputs_manifest=manifest,merge_dir=merge)
        row['coverage_status']='merging'
        await runner.coverage_command(render(config['merge_command'],values),row,log)
        report=json.loads(Path(config['report_path'].format(**values)).read_text())
        from .coverage_schema import validate_report
        report=validate_report(report,row['build_id'])
        current.update(build_id=row['build_id'],runs=inputs,report=report,updated=time.time())
        atomic(Path(row['cwd'])/'.flowcanvas/coverage/state.json',current)
        row['coverage_status']='merged'


def public(root):
    """Return the project's published coverage batch without source-specific assumptions."""
    return state(root)
