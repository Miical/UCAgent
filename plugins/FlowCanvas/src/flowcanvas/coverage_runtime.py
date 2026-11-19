"""Shared database lifecycle and atomic coverage publication."""
import json
import shutil
from pathlib import Path
import tempfile
import time
import uuid
from .targets import atomic


def state(root):
    path=Path(root)/'output/flowcanvas/coverage/state.json'
    if not path.exists():atomic(path,dict(epoch=uuid.uuid4().hex,build_id=None,runs=[],report=None))
    return json.loads(path.read_text())


def reset(root):
    """Delete cumulative databases/reports while retaining run history and source snapshots."""
    folder=Path(root)/'output/flowcanvas/coverage'
    for child in folder.iterdir() if folder.exists() else []:
        if child.name in {'sources','config.json','build.json','targets.json'}:
            continue
        if child.name=='database' and child.is_dir() and not child.is_symlink():
            database=child/'coverage.vdb'
            compile_db=child/'compile.vdb'
            if not compile_db.exists() and not compile_db.is_symlink() and (database/'snps/coverage/db/design').is_dir():
                from .native_vcs import copy_compile_metadata
                with tempfile.TemporaryDirectory(prefix='.compile-',dir=child) as staging:
                    metadata=Path(staging)/'compile.vdb'
                    copy_compile_metadata(database,metadata)
                    metadata.rename(compile_db)
            if database.is_symlink() or database.is_file():database.unlink()
            elif database.is_dir():shutil.rmtree(database)
            continue  # Compile metadata must survive reset for the next simulation.
        if child.is_dir() and not child.is_symlink():shutil.rmtree(child)
        else:child.unlink()
    data=dict(epoch=uuid.uuid4().hex,build_id=None,runs=[],report=None)
    atomic(Path(root)/'output/flowcanvas/coverage/state.json',data)
    return data


def render(command,context):
    import shlex
    for key,value in context.items():command=command.replace('{'+key+'}',shlex.quote(str(value)))
    return command


def context(row):
    """Resolve service-owned database paths and unique names for command templates."""
    return dict(project=row['cwd'],run_dir=row['run_dir'],case=row['entry'],
                coverage_db=row['coverage_db'],coverage_name=row['id'],
                wave_dir=str(Path(row['run_dir'])/'waveforms'))


async def collect(runner,row,log):
    from .coverage_links import save_hits
    config=row['coverage_config']
    if config.get('provider')=='toffee':
        import asyncio
        from .native_toffee import publish_run, smoke_report
        if row.get('mode')=='coverage_smoke':
            from .smoke_runs import validate_smoke_report
            raw=await asyncio.to_thread(smoke_report,row['cwd'],row['run_dir'],row['build_id'])
            report=validate_smoke_report(row['cwd'],raw,native_toffee=True)
            artifact=Path(row['run_dir'])/'coverage.json'
            atomic(artifact,report)
            await asyncio.to_thread(save_hits,row['run_dir'],report,row['cwd'])
            row.update(coverage_status='validated',coverage_artifact=str(artifact))
        else:
            artifact=await asyncio.to_thread(publish_run,row['cwd'],row['run_dir'],row['build_id'])
            row.update(coverage_status='published',coverage_artifact=str(artifact))
        return
    values=context(row)
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
        save_hits(row['run_dir'],report,row['cwd'])
        row.update(coverage_status='validated',coverage_artifact=str(artifact))
        return
    from .coverage_schema import validate_report
    if config.get('provider')=='vcs' or config.get('mode','per_run')=='per_run':
        save_hits(row['run_dir'],validate_report(json.loads(artifact.read_text()),row['build_id']),row['cwd'])
    async with runner.lock:
        current=state(row['cwd'])
        if current['epoch']!=row['coverage_epoch']:
            row['coverage_status']='previous_batch';return
        if current.get('mode', 'per_run')=='shared' and config.get('mode','per_run')!='shared':
            raise ValueError('Coverage mode changed; clear accumulated coverage before collecting')
        if current['build_id'] not in (None,row['build_id']):raise ValueError('构建版本变化，请清空累计后重新运行')
        if row['id'] in [r['id'] for r in current['runs']]:return
        inputs=current['runs']+[dict(id=row['id'],artifact=str(artifact))]
        if config.get('provider')=='vcs':
            import asyncio
            from .native_vcs import accumulate, external_test_ids, fingerprint, managed_databases, writer_active
            folder=Path(row['cwd'])/'output/flowcanvas/coverage'
            if await asyncio.to_thread(writer_active,row['cwd']):
                raise ValueError('Wait for the external VCS writer to finish before publishing coverage')
            token=await asyncio.to_thread(fingerprint,row['cwd'])
            inputs[-1]['isolated_vdb']=True
            output=folder/'reports'/uuid.uuid4().hex/'coverage.json'
            external=folder/'database/coverage.vdb'
            report, database=await accumulate(row['cwd'],external,
                managed_databases(row['cwd'],inputs),output,row['build_id'],token)
            previous_database=current.get('merged_database')
            if token:
                known={run['id'] for run in inputs}
                inputs.extend(dict(id=identity,artifact=str(output),origin='database')
                              for identity in external_test_ids(external) if identity not in known)
            current.update(build_id=row['build_id'],runs=inputs,report=report,updated=time.time(),
                           database_fingerprint=token,merged_database=database,ingestion_status='ready')
            for key in ('ingestion_error','failed_fingerprint','failed_at'):current.pop(key,None)
            atomic(folder/'state.json',current)
            if previous_database:
                previous=Path(previous_database)
                if previous.parent.parent==folder/'reports':
                    await asyncio.to_thread(shutil.rmtree,previous,True)
            row.update(coverage_status='merged',coverage_artifact=str(artifact))
            return
        if config.get('mode', 'per_run') == 'shared':
            report=validate_report(json.loads(artifact.read_text()),row['build_id'])
            current.update(build_id=row['build_id'],runs=inputs,report=report,updated=time.time())
            atomic(Path(row['cwd'])/'output/flowcanvas/coverage/state.json',current)
            row['coverage_status']='merged'
            return
        merge=Path(row['cwd'])/'output/flowcanvas/coverage'/current['epoch']/uuid.uuid4().hex
        merge.mkdir(parents=True)
        manifest=merge/'inputs.json';atomic(manifest,[r['artifact'] for r in inputs])
        values.update(inputs_manifest=manifest,merge_dir=merge)
        row['coverage_status']='merging'
        await runner.coverage_command(render(config['merge_command'],values),row,log)
        report=json.loads(Path(config['report_path'].format(**values)).read_text())
        report=validate_report(report,row['build_id'])
        current.update(build_id=row['build_id'],runs=inputs,report=report,updated=time.time())
        atomic(Path(row['cwd'])/'output/flowcanvas/coverage/state.json',current)
        row['coverage_status']='merged'


def public(root):
    """Return the project's published coverage batch without source-specific assumptions."""
    from . import native_toffee
    if native_toffee.enabled(root):
        return native_toffee.public(root)
    current = state(root)
    if (current.get('report') or {}).get('provider') == 'toffee':
        return {**current, 'provider': 'toffee'}
    definition = Path(root) / '.flowcanvas/testcases.json'
    if definition.is_file():
        config = json.loads(definition.read_text()).get('coverage') or {}
        if config.get('provider') != 'vcs' and config.get('tool') == 'verilator-toffee':
            return {**current, 'provider': 'toffee'}
    return current
