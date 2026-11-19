"""Persist and schedule one user-selected batch per project."""
import asyncio
import json
import os
from pathlib import Path
import time
import uuid

from .targets import atomic
from .testcases import Cases

MAX_CONCURRENCY = 4


def concurrency_limit(coverage):
    """A command-based shared writer keeps its existing exclusive access."""
    return 1 if coverage and coverage.provider == 'commands' and coverage.mode == 'shared' else MAX_CONCURRENCY


class TestBatches:
    """Use the existing case runner for each attempt and retain queue state on disk."""

    def __init__(self, runner):
        """Own the active coordinators separately from individual processes."""
        self.runner = runner
        self.active = {}

    def save(self, root, batch):
        """Atomically save the current queue and run IDs."""
        atomic(self.runner.folder(root)/'batches'/(batch['id']+'.json'), batch)

    async def start(self, root, case_ids, concurrency):
        """Reserve the project and begin up to the requested number of cases."""
        root = Path(root).resolve()
        async with self.runner.lock:
            if root in self.active or any(row['id'] in self.runner.tasks for row in self.runner.list(root)):
                raise ValueError('本项目已有测试运行，请等待结束')
            data = Cases(root).read()
            limit = concurrency_limit(data.coverage)
            case_ids = list(dict.fromkeys(case_ids))
            if not case_ids:
                raise ValueError('请选择测试用例')
            if concurrency == 'auto':
                try:
                    cpu_count = len(os.sched_getaffinity(0))
                except (AttributeError,OSError):
                    cpu_count = os.cpu_count() or 1
                lanes = min(max(1,cpu_count),limit,len(case_ids))
            else:
                if not 1 <= concurrency <= limit:
                    raise ValueError(f'此项目支持 1–{limit} 路并发')
                lanes = min(concurrency,len(case_ids))
            cards = {card.id: card for card in data.targets}
            items = []
            for case_id in case_ids:
                card = cards.get(case_id)
                if not card or card.lifecycle != 'ready' or not card.runner:
                    raise ValueError('所选测试用例无法运行，请检查状态与运行命令')
                if lanes > 1 and not any(token in card.runner.command for token in ('{run_dir}', '{wave_dir}', '{coverage_db}')):
                    raise ValueError('并发用例的运行命令须使用独立产物路径：'+(card.title or card.name))
                items.append(dict(case_id=case_id, name=card.title or card.name, status='queued'))
            batch = dict(id=uuid.uuid4().hex, status='running', started=time.time(),
                         concurrency=lanes, requested_concurrency=concurrency, items=items)
            self.save(root,batch)
            self.active[root] = batch, asyncio.create_task(self.execute(root,batch))
            return batch

    async def execute(self, root, batch):
        """Continue processing the queue after individual validation or test failures."""
        queue = iter(batch['items'])

        async def worker():
            """Run one lane of the shared queue until it is drained or stopped."""
            for item in queue:
                if batch['status'] != 'running':
                    break
                try:
                    row = await self.runner.start(root,item['case_id'],batch=batch)
                except (ValueError,OSError,SyntaxError) as error:
                    if batch['status'] == 'running':
                        item.update(status='rejected',error=str(error))
                    else:
                        item['status']='cancelled'
                    self.save(root,batch)
                    continue
                item.update(status='started',run_id=row['id'])
                self.save(root,batch)
                await asyncio.gather(self.runner.tasks[row['id']],return_exceptions=True)

        try:
            await asyncio.gather(*(worker() for _ in range(batch['concurrency'])))
        finally:
            for item in batch['items']:
                if item['status']=='queued':
                    item['status']='cancelled'
            batch.update(status='completed' if batch['status']=='running' else 'cancelled', ended=time.time())
            self.save(root,batch)
            self.active.pop(root,None)

    def latest(self, root):
        """Return the latest persisted batch with its individual run outcomes."""
        root = Path(root).resolve()
        active = self.active.get(root)
        if active:
            batch = active[0]
        else:
            files = (self.runner.folder(root)/'batches').glob('*.json')
            file = max(files,key=lambda path:path.stat().st_mtime_ns,default=None)
            if not file:
                return None
            batch = json.loads(file.read_text())
            if batch['status'] in {'running','stopping'}:
                batch.update(status='interrupted',ended=time.time())
                for item in batch['items']:
                    if item['status']=='queued':
                        item['status']='cancelled'
                self.save(root,batch)
        runs = {row['id']:row for row in self.runner.list(root)}
        items = []
        for item in batch['items']:
            row = runs.get(item.get('run_id'))
            if not row:
                items.append(item)
                continue
            result = self.runner.result(root,row)
            items.append({**item,'status':row['status'],'result':result['status'],
                          'error':row.get('error'),'coverage_status':row.get('coverage_status')})
        return {**batch,'items':items}

    async def stop(self, root, identity):
        """Cancel queued entries and stop the active simulations in this batch."""
        root = Path(root).resolve()
        async with self.runner.lock:
            active = self.active.get(root)
            if not active or active[0]['id']!=identity:
                return self.latest(root)
            batch,task = active
            batch['status']='stopping'
            runs = [item['run_id'] for item in batch['items'] if item.get('run_id') in self.runner.tasks]
            self.save(root,batch)
        await asyncio.gather(*(self.runner.stop(root,run_id) for run_id in runs))
        await task
        return self.latest(root)

    async def close(self):
        """Drain active batch coordinators before shutting down the runner."""
        await asyncio.gather(*(self.stop(root,batch['id']) for root,(batch,_) in list(self.active.items())))
