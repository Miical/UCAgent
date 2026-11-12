"""Debounced native database ingestion, independent of test command completion callbacks."""
import asyncio
import json
from pathlib import Path
import time
import uuid

from .coverage_runtime import state
from .native_vcs import build_identity, configuration, extract, fingerprint, writer_active
from .targets import atomic


class CoverageWatch:
    """Refresh stable project databases while preserving accepted reports on errors."""

    def __init__(self, projects, runner, settle_seconds=3):
        """Share the runner lock so reset and managed writers cannot race ingestion."""
        self.projects=projects;self.runner=runner;self.settle_seconds=settle_seconds;self.observed={}

    async def tick(self, root):
        """Ingest a changed snapshot only after quiescence and without an active managed run."""
        root=Path(root).resolve();folder=root/'.flowcanvas/coverage'
        if not (folder/'config.json').exists():return
        if any(r['id'] in self.runner.tasks or r['status'] in {'starting','running'} for r in self.runner.list(root)):return
        if await asyncio.to_thread(writer_active,root):return
        config=configuration(root)
        if config.get('provider')!='vcs':return
        current=state(root);token=await asyncio.to_thread(fingerprint,root)
        if token is None or current.get('database_fingerprint')==token:return
        now=time.monotonic();previous=self.observed.get(str(root))
        if previous is None or previous[0]!=token:
            self.observed[str(root)]=(token,now);return
        if now-previous[1]<self.settle_seconds:return
        if current.get('failed_fingerprint')==token and time.time()-current.get('failed_at',0)<30:return
        async with self.runner.lock:
            if any(r['id'] in self.runner.tasks for r in self.runner.list(root)):return
            current=state(root);epoch=current['epoch']
            output=folder/'reports'/uuid.uuid4().hex/'coverage.json'
            try:
                identity=await asyncio.to_thread(build_identity,root)
                if current['build_id'] not in (None,identity):
                    raise ValueError('Coverage build changed; clear accumulated coverage before publishing')
                report=await extract(root,folder/'database/coverage.vdb',output,identity)
                if await asyncio.to_thread(writer_active,root) or await asyncio.to_thread(fingerprint,root)!=token or await asyncio.to_thread(build_identity,root)!=identity:
                    self.observed.pop(str(root),None);return
                latest=state(root)
                if latest['epoch']!=epoch:return
                tests=folder/'database/coverage.vdb/snps/coverage/db/testdata'
                runs=list(latest['runs']);known={r['id'] for r in runs}
                for path in sorted(tests.iterdir()):
                    if path.is_dir() and path.name not in known:
                        runs.append(dict(id=path.name,artifact=str(output),origin='database'))
                latest.update(report=report,build_id=identity,mode='shared',runs=runs,updated=time.time(),
                              database_fingerprint=token,ingestion_status='ready')
                for key in ('ingestion_error','failed_fingerprint','failed_at'):latest.pop(key,None)
                atomic(folder/'state.json',latest)
            except Exception as exc:
                latest=state(root)
                if latest['epoch']==epoch:
                    latest.update(ingestion_status='failed',ingestion_error=str(exc),
                                  failed_fingerprint=token,failed_at=time.time())
                    atomic(folder/'state.json',latest)

    async def run(self):
        """Observe configured recent projects until application shutdown cancels this task."""
        while True:
            for project in list(self.projects.state.get('recent',[])):
                try:await self.tick(project['path'])
                except (OSError,ValueError,KeyError):continue
            await asyncio.sleep(2)
