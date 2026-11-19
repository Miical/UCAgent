"""Integration regressions for multi-case scheduling and interruption."""
import asyncio
import shlex
import sys

import pytest

from flowcanvas.test_runner import TestRuns as Runs
from flowcanvas.testcases import Cases, execute


def cases(root, delay, outcomes):
    """Register executable cases with private output and predictable outcomes."""
    def call(operation, **fields):
        """Run a card registration step at the current revision."""
        return execute(root,dict(operation=operation,expected_revision=Cases(root).read().revision,**fields),'batch')
    identities=call('apply',count=len(outcomes))['ids']
    call('begin_modify',ids=identities)
    cards=[]
    for index,(identity,exit_code) in enumerate(zip(identities,outcomes)):
        name=f'test_{index}'
        source=root/(name+'.py')
        code=f'import sys,time\nfrom pathlib import Path\nPath(sys.argv[1],"ran").touch()\ntime.sleep({delay})\nprint("TEST PASSED")\nraise SystemExit({exit_code})\n'
        source.write_text(code)
        cards.append(dict(id=identity,name=name,title=name,description='Batch fixture',
            bindings=[dict(file=source.name,line=1,line_count=len(code.splitlines()))],
            runner=dict(command=shlex.quote(sys.executable)+' '+source.name+' {run_dir} {case}',description='Batch fixture')))
    call('update',cards=cards)
    call('end_modify',ids=identities)
    return identities


@pytest.mark.asyncio
async def test_parallel_queue_deduplicates_and_continues_after_failure(tmp_path):
    """Two lanes overlap; a third case runs even when an earlier case fails."""
    identities=cases(tmp_path,.4,[0,1,0]);runner=Runs()
    try:
        batch=await runner.batches.start(tmp_path,[*identities,identities[0]],2)
        assert len(batch['items'])==3
        with pytest.raises(ValueError,match='已有测试运行'):
            await runner.start(tmp_path,identities[0])
        await asyncio.wait_for(runner.batches.active[tmp_path][1],8)
        batch=runner.batches.latest(tmp_path)
        assert batch['status']=='completed'
        assert [item['status'] for item in batch['items']]==['completed','failed','completed']
        by_case={row['case_id']:row for row in runner.list(tmp_path)}
        first,second,third=(by_case[identity] for identity in identities)
        assert second['started'] < first['ended']
        assert third['started'] >= min(first['ended'],second['ended'])
        assert len({row['run_dir'] for row in by_case.values()})==3
        assert all(row['batch_id']==batch['id'] for row in by_case.values())
        assert Runs().batches.latest(tmp_path)==batch
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_stop_batch_interrupts_workers_and_never_starts_waiting_cases(tmp_path):
    """Stopping a batch preserves real run records and cancels the rest of its queue."""
    identities=cases(tmp_path,30,[0,0,0]);runner=Runs()
    try:
        batch=await runner.batches.start(tmp_path,identities,2)
        for _ in range(100):
            if len(runner.processes)==2:
                break
            await asyncio.sleep(.02)
        assert len(runner.processes)==2
        result=await asyncio.wait_for(runner.batches.stop(tmp_path,batch['id']),8)
        assert result['status']=='cancelled'
        assert [item['status'] for item in result['items']]==['interrupted','interrupted','cancelled']
        assert not runner.tasks and not runner.batches.active
    finally:
        await runner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(('cpu_count','selected_count','expected'),[(2,3,2),(8,2,2),(8,5,4)])
async def test_auto_concurrency_respects_cpu_selection_and_backend_limit(tmp_path,monkeypatch,cpu_count,selected_count,expected):
    """Resolve auto at launch, using the host CPU affinity and deduplicated selection."""
    monkeypatch.setattr('flowcanvas.test_batches.os.sched_getaffinity',lambda _pid:set(range(cpu_count)),raising=False)
    runner=Runs()
    identities=cases(tmp_path,.05,[0]*selected_count)
    try:
        batch=await runner.batches.start(tmp_path,[*identities,identities[0]],'auto')
        assert batch['requested_concurrency']=='auto'
        assert batch['concurrency']==expected
        await asyncio.wait_for(runner.batches.active[tmp_path][1],8)
        assert runner.batches.latest(tmp_path)['concurrency']==expected
    finally:
        await runner.close()
