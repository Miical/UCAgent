"""One managed command entry point for UI and CLI test runs."""
import argparse
import asyncio
import codecs
import json
import os
from pathlib import Path
import re
import signal
import time
import uuid
import urllib.request
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from .targets import atomic
from .testcases import Cases


class TestRuns:
    def __init__(self):
        self.tasks = {}
        self.processes = {}
        self.lock = asyncio.Lock()

    def folder(self, root):
        path = Path(root)/'.flowcanvas/test-runs'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list(self, root):
        rows = []
        for path in self.folder(root).glob('*.json'):
            try: row = json.loads(path.read_text())
            except (OSError, ValueError): continue
            if row['status'] in {'starting','running'} and row['id'] not in self.tasks:
                row.update(status='interrupted',ended=time.time()); atomic(path,row)
            rows.append(row)
        return sorted(rows,key=lambda r:r['started'],reverse=True)

    def output(self, root, run_id, offset=0):
        """Read a bounded UTF-8 log chunk belonging to this project run."""
        if not re.fullmatch('[0-9a-f]{32}', run_id) or offset < 0:
            raise ValueError('无效运行或偏移量')
        folder = self.folder(root); meta = folder / (run_id + '.json')
        if not meta.is_file():
            raise ValueError('运行不存在')
        row = json.loads(meta.read_text()); log = folder / (run_id + '.log')
        text = ''; next_offset = offset
        if log.exists():
            with log.open('rb') as stream:
                stream.seek(offset); chunk = stream.read(65536)
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            text = decoder.decode(chunk, final=row['status'] not in {'starting', 'running'})
            next_offset += len(chunk) - len(decoder.getstate()[0])
        return dict(run=row, text=text, offset=next_offset)

    async def stop(self, root, run_id):
        """Cancel only a verified run in the bound project, retaining logs and history."""
        self.output(root, run_id)
        task = self.tasks.get(run_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.output(root, run_id)

    async def start(self, root, case_id, mode="test", ready_pattern="", startup_timeout=120):
        if mode not in {'test','startup_smoke','coverage_smoke'}:
            raise ValueError('Unknown run mode')
        if mode in {'startup_smoke','coverage_smoke'}:
            if not ready_pattern or len(ready_pattern)>500 or not 1<=startup_timeout<=600:
                raise ValueError('Startup smoke needs a test-start log pattern and a 1..600 second timeout')
            re.compile(ready_pattern)
        async with self.lock:
            store=Cases(root); data=store.read()
            if any(r['status'] in {'starting','running'} for r in self.list(root)):
                raise ValueError('本项目已有命令运行中，请等待完成，避免覆盖共享构建和日志')
            if mode=='coverage_smoke' and (data.coverage is None or data.coverage.mode!='shared'):
                raise ValueError('Coverage smoke requires configured shared coverage')
            case=next((n for n in data.targets if n.id==case_id),None)
            if not case: raise ValueError('测试用例不存在')
            template=case.runner
            if not template: raise ValueError('尚未配置此测试用例的运行命令，请更新该卡片 runner')
            if case.lifecycle != 'ready': raise ValueError('用例正在创建或删除，尚不可运行')
            binding=next((b for b in case.bindings if b.role=='definition'),None)
            if not binding: raise ValueError('测试用例尚未绑定源码')
            report=store.status()['reports'][case.id]
            if report['status'] != 'bound':
                raise ValueError('Case source changed or binding is invalid; inspect and update it first')
            store.validate_card(case)
            node=next(n for n in report['bindings'] if n['id']==binding.id)
            entry=case.name
            if binding.file.endswith('.py'):
                entry=str(store.file(binding.file))+'::'+case.name.replace('.', '::')
            # Values are shell-quoted once by this entry point, never interpolated by the model.
            run_id=uuid.uuid4().hex; folder=self.folder(root)
            run_dir=folder/run_id;run_dir.mkdir()
            from .coverage_runtime import render
            database=(run_dir/'coverage.vdb' if mode in {'startup_smoke','coverage_smoke'} else
                      store.root/'.flowcanvas/coverage/database/coverage.vdb')
            if data.coverage and data.coverage.mode=='shared':
                if not all(token in template.command for token in ('{coverage_db}','{coverage_name}')):
                    raise ValueError('Shared coverage runner requires {coverage_db} and {coverage_name}')
            command=render(template.command,dict(project=store.root,run_dir=run_dir,case=entry,
                                                coverage_db=database,coverage_name=run_id))
            row=dict(id=run_id,case_id=case_id,name=case.title or case.name,entry=entry,command=command,
                     cwd=str(store.root),description=template.description,status='starting',started=time.time(),
                     ended=None,exit_code=None,source_fingerprint=node['fingerprint'])
            row['run_dir']=str(run_dir)
            row['coverage_db']=str(database)
            if mode in {'startup_smoke','coverage_smoke'}:
                from .smoke_runs import case_identity
                row.update(mode=mode,smoke_status='pending',ready_pattern=ready_pattern,
                           startup_timeout=startup_timeout,case_identity=case_identity(case))
            if data.coverage and mode in {'test','coverage_smoke'}:
                from .coverage_runtime import state
                row.update(coverage_config=data.coverage.model_dump(),coverage_epoch=state(root)['epoch'],coverage_status='pending')
                if mode=='coverage_smoke':
                    from .smoke_runs import coverage_identity
                    row['coverage_identity']=coverage_identity(row['coverage_config'])
            atomic(folder/(run_id+'.json'),row)
            task=asyncio.create_task(self.execute(folder,row))
            self.tasks[run_id]=task
            task.add_done_callback(lambda _:self.tasks.pop(run_id,None))
            return row

    async def execute(self,folder,row):
        if row.get('mode')=='startup_smoke':
            from .smoke_runs import execute_smoke
            return await execute_smoke(self,folder,row)
        path=folder/(row['id']+'.json')
        try:
            with (folder/(row['id']+'.log')).open('ab',buffering=0) as log:
                if row.get('coverage_config'):
                    from .coverage_runtime import render, context, state
                    if row['coverage_config'].get('provider')=='vcs':
                        from .native_vcs import build_identity, metadata_identity
                        row['build_id']=await asyncio.to_thread(build_identity,row['cwd'])
                        row['native_coverage_identity']=await asyncio.to_thread(metadata_identity,row['cwd'])
                    else:
                        row['build_id']=(await self.coverage_command(render(row['coverage_config']['build_id_command'],context(row)),row,log)).strip()
                    if not row['build_id']:raise ValueError('构建标识为空')
                    if row['coverage_config'].get('mode','per_run')=='shared' and row.get('mode')!='coverage_smoke':
                        async with self.lock:
                            current=state(row['cwd'])
                            if current.get('mode', 'per_run' if current['runs'] else 'shared')!='shared':
                                raise ValueError('Coverage mode changed; clear accumulated coverage before running')
                            if current['build_id'] not in (None,row['build_id']):
                                raise ValueError('Coverage build changed; clear accumulated coverage before running')
                            current.update(build_id=row['build_id'],mode='shared')
                            atomic(Path(row['cwd'])/'.flowcanvas/coverage/state.json',current)
                            Path(row['coverage_db']).parent.mkdir(parents=True,exist_ok=True)
                process=await asyncio.create_subprocess_exec('/bin/bash','-lc',row['command'],cwd=row['cwd'],
                    env=dict(os.environ,PYTHONUNBUFFERED='1',TERM='dumb',FLOWCANVAS_RUN_DIR=row['run_dir']),stdout=log,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
                self.processes[row['id']]=process
                row['status']='running';atomic(path,row)
                if row.get('mode')=='coverage_smoke':
                    try:
                        row['exit_code']=await asyncio.wait_for(process.wait(),row['startup_timeout'])
                    except asyncio.TimeoutError:
                        from .smoke_runs import terminate
                        await terminate(process)
                        row['exit_code']=process.returncode
                        raise ValueError('Coverage smoke timed out before natural completion')
                    from .smoke_runs import startup_error
                    text=(folder/(row['id']+'.log')).read_text(errors='replace')
                    if row['exit_code']!=0 or startup_error(text):
                        raise ValueError(startup_error(text) or 'Coverage smoke exited with code '+str(row['exit_code']))
                    if not re.search(row['ready_pattern'],text):
                        raise ValueError('Coverage smoke lacks actual test-execution evidence')
                    if row['coverage_config'].get('provider')=='vcs':
                        actual=await asyncio.to_thread(build_identity,row['cwd'])
                    else:
                        actual=(await self.coverage_command(render(row['coverage_config']['build_id_command'],context(row)),row,log)).strip()
                    if actual!=row['build_id']:
                        raise ValueError('Build changed during coverage smoke; prepare the build before running')
                    row['stop_reason']='natural_exit'
                else:
                    row['exit_code']=await process.wait()
                if row.get('coverage_config'):
                    from .coverage_runtime import collect
                    try:await collect(self,row,log)
                    except Exception as exc:
                        row.update(coverage_status='failed',coverage_error=str(exc));log.write(('\n[覆盖率] '+str(exc)+'\n').encode())
        except asyncio.CancelledError:
            process=self.processes.get(row['id'])
            if process and process.returncode is None:
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:await asyncio.wait_for(process.wait(),3)
                except asyncio.TimeoutError:
                    os.killpg(process.pid,signal.SIGKILL);await process.wait()
            row['status']='interrupted'
        except Exception as exc:
            row['status']='failed';row['error']=str(exc)
            if row.get('coverage_config'):row.update(coverage_status='failed',coverage_error=str(exc))
        finally:
            if row['status']=='running':
                row['status']='completed' if row['exit_code']==0 else 'failed'
            if row.get('mode')=='coverage_smoke':
                from .smoke_runs import startup_error
                error=startup_error((folder/(row['id']+'.log')).read_text(errors='replace'))
                if error:
                    row.update(status='failed',error=error)
                row['smoke_status']='passed' if row['status']=='completed' and row.get('coverage_status')=='validated' else 'cancelled' if row['status']=='interrupted' else 'failed'
            row['ended']=time.time();atomic(path,row);self.processes.pop(row['id'],None)

    async def coverage_command(self,command,row,log):
        process=await asyncio.create_subprocess_exec('/bin/bash','-lc',command,cwd=row['cwd'],
            env=dict(os.environ,PYTHONUNBUFFERED='1'),stdout=asyncio.subprocess.PIPE,stderr=log,start_new_session=True)
        self.processes[row['id']]=process
        try:
            stdout,_=await asyncio.wait_for(process.communicate(),180)
        except asyncio.TimeoutError:
            os.killpg(process.pid,signal.SIGKILL);await process.wait();raise ValueError('Coverage command timed out')
        if process.returncode:raise ValueError('Coverage command failed with exit '+str(process.returncode))
        return stdout.decode('utf-8',errors='replace')

    async def close(self):
        tasks=list(self.tasks.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


def routes(projects,runner):
    router=APIRouter(prefix='/api/chat/projects')
    def root(identity):
        project=next((p for p in projects.state['recent'] if p['id']==identity),None)
        if not project:raise HTTPException(404,'项目不存在')
        return project['path']
    class RunRequest(BaseModel):
        case_id:str
    @router.get('/{identity}/coverage')
    def coverage(identity:str):
        from .coverage_runtime import public
        return public(root(identity))
    @router.get('/{identity}/coverage/source')
    async def coverage_source(identity:str,instance:str,metric:str="line",file:str|None=None):
        from .coverage_runtime import state
        import hashlib
        data=state(root(identity));report=data.get('report') or {}
        if metric == "line":
            row=next((n for n in report.get('instances',[]) if n['path']==instance),None)
            if not row:raise HTTPException(404,'实例没有覆盖率源码')
            files=row.get('files',[])
            source=next((entry for entry in files if not file or entry["path"]==file),None)
        else:
            if metric not in {"toggle","branch","expr","cond","fsm","assert"}:
                raise HTTPException(422,"Unknown coverage metric")
            items=report.get("code_metrics",{}).get(metric,{}).get("dut",{}).get("items",[])
            entry=next((item for item in items if (not item.get("hierarchy") or item["hierarchy"]==instance) and (not file or item["file"]==file)),None)
            source={"path":entry["file"],"sha256":entry["sha256"]} if entry else None
            row={"module":(entry or {}).get("module") or instance.split(".")[-1]}
        if not source:raise HTTPException(404,'覆盖率报告没有此源码映射')
        expected=source.get('sha256') or report.get('rtl_sha256','')
        if not re.fullmatch('[0-9a-f]{64}',expected):raise HTTPException(422,'源码指纹缺失')
        project=Path(root(identity)).resolve()
        file=source['path']
        candidate=(project/file).resolve()
        if not candidate.is_relative_to(project):raise HTTPException(422,'源码路径不属于项目')
        cache=project/'.flowcanvas/coverage/sources'/(expected+Path(file).suffix)
        path=cache if cache.is_file() else candidate
        if not path.is_file():raise HTTPException(404,'构建源码快照不可用，请配置覆盖率适配器保存源码')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected:raise HTTPException(422,'源码已变化，与构建指纹不符')
        text=raw.decode()
        match=re.search(r'\bmodule\s+'+re.escape(row['module'])+r'\b[\s\S]*?\bendmodule\b',text)
        return dict(file=file,module=row['module'],content=text,
                    line=text.count('\n',0,match.start())+1 if match else 1,
                    end_line=text.count('\n',0,match.end())+1 if match else len(text.splitlines()))
    @router.post('/{identity}/coverage/reset')
    async def clear_coverage(identity:str):
        from .coverage_runtime import reset
        async with runner.lock:
            project=root(identity)
            if any(run['id'] in runner.tasks or run['status'] in {'starting','running'} for run in runner.list(project)):
                raise HTTPException(409,'Cannot clear accumulated coverage while a test is active')
            return reset(project)
    @router.get('/{identity}/test-runs')
    def runs(identity:str):return [run for run in runner.list(root(identity)) if run.get('mode') not in {'startup_smoke','coverage_smoke'}]
    @router.post('/{identity}/test-runs')
    async def start(identity:str,request:RunRequest):
        try:return await runner.start(root(identity),request.case_id)
        except (ValueError,OSError,SyntaxError) as exc:raise HTTPException(422,str(exc)) from None
    @router.get('/{identity}/test-runs/{run_id}/output')
    def output(identity:str,run_id:str,offset:int=0):
        try: return runner.output(root(identity), run_id, offset)
        except ValueError as exc: raise HTTPException(400, str(exc)) from None

    return router


def main():
    parser=argparse.ArgumentParser(description='Start a test through the FlowCanvas managed command entry')
    parser.add_argument('--project',required=True);parser.add_argument('--case',required=True,help='Stable tc_ ID')
    parser.add_argument('--server',default='http://127.0.0.1:8810')
    args=parser.parse_args()
    marker=json.loads((Path(args.project)/'.flowcanvas/project.json').read_text())
    request=urllib.request.Request(args.server+'/api/chat/projects/'+marker['id']+'/test-runs',
        data=json.dumps({'case_id':args.case}).encode(),headers={'Content-Type':'application/json'})
    print(urllib.request.urlopen(request).read().decode())

if __name__=='__main__':main()
