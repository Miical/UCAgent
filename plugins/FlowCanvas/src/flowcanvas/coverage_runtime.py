"""Coverage epochs and atomic publication of independently generated reports."""
import hashlib
import json
from pathlib import Path
import time
import uuid
from .targets import atomic


def state(root):
    path=Path(root)/'.flowcanvas/coverage/state.json'
    if not path.exists():atomic(path,dict(epoch=uuid.uuid4().hex,build_id=None,runs=[],report=None))
    return json.loads(path.read_text())


def reset(root):
    data=dict(epoch=uuid.uuid4().hex,build_id=None,runs=[],report=None)
    atomic(Path(root)/'.flowcanvas/coverage/state.json',data)
    return data


def render(command,context):
    import shlex
    for key,value in context.items():command=command.replace('{'+key+'}',shlex.quote(str(value)))
    return command


async def collect(runner,row,log):
    config=row['coverage_config'];context=dict(project=row['cwd'],run_dir=row['run_dir'],case=row['entry'])
    row['coverage_status']='extracting'
    await runner.coverage_command(render(config['extract_command'],context),row,log)
    artifact=Path(config['artifact_path'].format(**context))
    if not artifact.is_file():raise ValueError('Coverage artifact missing')
    async with runner.lock:
        current=state(row['cwd'])
        if current['epoch']!=row['coverage_epoch']:
            row['coverage_status']='previous_batch';return
        if current['build_id'] not in (None,row['build_id']):raise ValueError('构建版本变化，请清空累计后重新运行')
        if row['id'] in [r['id'] for r in current['runs']]:return
        inputs=current['runs']+[dict(id=row['id'],artifact=str(artifact))]
        merge=Path(row['cwd'])/'.flowcanvas/coverage'/current['epoch']/uuid.uuid4().hex
        merge.mkdir(parents=True)
        manifest=merge/'inputs.json';atomic(manifest,[r['artifact'] for r in inputs])
        context.update(inputs_manifest=manifest,merge_dir=merge)
        row['coverage_status']='merging'
        await runner.coverage_command(render(config['merge_command'],context),row,log)
        report=json.loads(Path(config['report_path'].format(**context)).read_text())
        if report.get('schema_version')!=1 or report.get('build_id')!=row['build_id'] or not report.get('instances'):
            raise ValueError('Invalid or incompatible coverage report')
        for n in report['instances']:
            if not isinstance(n.get('covered'),int) or not isinstance(n.get('total'),int) or not 0<=n['covered']<=n['total']:
                raise ValueError('Invalid coverage counts')
        current.update(build_id=row['build_id'],runs=inputs,report=report,updated=time.time())
        atomic(Path(row['cwd'])/'.flowcanvas/coverage/state.json',current)
        row['coverage_status']='merged'


def public(root):
    data=state(root);report=data.get('report');rtl=Path(root)/'Cache.v'
    if report and rtl.is_file():
        data['source_matches']=hashlib.sha256(rtl.read_bytes()).hexdigest()==report.get('rtl_sha256')
    return data
