"""One managed command entry point for UI and CLI test runs."""
import argparse
import asyncio
import codecs
import json
import os
from pathlib import Path
import re
import shlex
import signal
import time
import uuid
import urllib.request
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from .targets import atomic
from .testcases import Cases
from .target_syntax import inventory


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

    async def start(self, root, case_id):
        async with self.lock:
            store=Cases(root); data=store.read()
            template=data.runner
            if not template: raise ValueError('尚未配置项目运行模板，请让 AI 先配置 runner')
            if any(r['status'] in {'starting','running'} for r in self.list(root)):
                raise ValueError('本项目已有命令运行中，请等待完成，避免覆盖共享构建和日志')
            case=next((n for n in data.targets if n.id==case_id),None)
            if not case: raise ValueError('测试用例不存在')
            if case.lifecycle != 'ready': raise ValueError('用例正在创建或删除，尚不可运行')
            binding=next((b for b in case.bindings if b.role=='definition'),None)
            if not binding: raise ValueError('测试用例尚未绑定源码')
            matches=[n for n in inventory(store.file(binding.file))['nodes'] if n['id']==binding.id]
            if len(matches)!=1: raise ValueError('用例源码绑定失效，请先核对')
            node=matches[0]
            entry=node['name']
            if binding.file.endswith('.py'):
                entry=binding.file+'::'+('::'.join(node['scope'].split('.'))+'::' if node['scope'] else '')+entry
            # Values are shell-quoted once by this entry point, never interpolated by the model.
            run_id=uuid.uuid4().hex; folder=self.folder(root)
            run_dir=folder/run_id;run_dir.mkdir()
            command=template.command.replace('{run_dir}',shlex.quote(str(run_dir))).replace('{case}',shlex.quote(entry)).replace('{project}',shlex.quote(str(store.root)))
            row=dict(id=run_id,case_id=case_id,name=case.title or case.name,entry=entry,command=command,
                     cwd=str(store.root),description=template.description,status='starting',started=time.time(),
                     ended=None,exit_code=None,source_fingerprint=node['fingerprint'])
            row['run_dir']=str(run_dir)
            if data.coverage:
                from .coverage_runtime import state
                row.update(coverage_config=data.coverage.model_dump(),coverage_epoch=state(root)['epoch'],coverage_status='pending')
            atomic(folder/(run_id+'.json'),row)
            task=asyncio.create_task(self.execute(folder,row))
            self.tasks[run_id]=task
            task.add_done_callback(lambda _:self.tasks.pop(run_id,None))
            return row

    async def execute(self,folder,row):
        path=folder/(row['id']+'.json')
        try:
            with (folder/(row['id']+'.log')).open('ab',buffering=0) as log:
                if row.get('coverage_config'):
                    from .coverage_runtime import render
                    row['build_id']=(await self.coverage_command(render(row['coverage_config']['build_id_command'],dict(project=row['cwd'],run_dir=row['run_dir'],case=row['entry'])),row,log)).strip()
                    if not row['build_id']:raise ValueError('构建标识为空')
                process=await asyncio.create_subprocess_exec('/bin/bash','-lc',row['command'],cwd=row['cwd'],
                    env=dict(os.environ,PYTHONUNBUFFERED='1',TERM='dumb'),stdout=log,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
                self.processes[row['id']]=process
                row['status']='running';atomic(path,row)
                row['exit_code']=await process.wait()
                row['status']='completed' if row['exit_code']==0 else 'failed'
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
    async def coverage_source(identity:str,instance:str):
        from .coverage_runtime import state
        import hashlib
        data=state(root(identity));report=data.get('report') or {}
        row=next((n for n in report.get('instances',[]) if n['path']==instance),None)
        if not row:raise HTTPException(404,'实例没有覆盖率源码')
        run_id=data['runs'][0]['id']
        if not re.fullmatch('[0-9a-f]{32}',run_id):raise HTTPException(422,'无效运行标识')
        expected=report.get('rtl_sha256','')
        if not re.fullmatch('[0-9a-f]{64}',expected):raise HTTPException(422,'源码指纹缺失')
        cache=Path(root(identity))/'.flowcanvas/coverage/sources'/(expected+'.v')
        if not cache.exists():
            process=await asyncio.create_subprocess_exec('ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','redmi',
                'cat /home/miical/eda/projects/uvm-cache/.flowcanvas/runs/'+run_id+'/Cache.v',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try:content,error=await asyncio.wait_for(process.communicate(),20)
            except asyncio.TimeoutError:
                process.kill();await process.wait();raise HTTPException(504,'读取构建源码超时')
            if process.returncode or hashlib.sha256(content).hexdigest()!=expected:raise HTTPException(422,'构建源码不可用或指纹不符')
            cache.parent.mkdir(parents=True,exist_ok=True);cache.write_bytes(content)
        raw=cache.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected:raise HTTPException(422,'源码缓存指纹不符')
        text=raw.decode();match=re.search(r'\bmodule\s+'+re.escape(row['module'])+r'\b[\s\S]*?\bendmodule\b',text)
        if not match:raise HTTPException(422,'构建源码中找不到模块')
        return dict(file='Cache.v',module=row['module'],content=text,line=text.count('\n',0,match.start())+1,end_line=text.count('\n',0,match.end())+1)
    @router.post('/{identity}/coverage/reset')
    async def clear_coverage(identity:str):
        from .coverage_runtime import reset
        async with runner.lock:return reset(root(identity))
    @router.get('/{identity}/test-runs')
    def runs(identity:str):return runner.list(root(identity))
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
