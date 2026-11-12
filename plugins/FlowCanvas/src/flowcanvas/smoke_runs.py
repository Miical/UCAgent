"""Bounded startup checks; successful termination never claims functional test pass."""
import asyncio
import hashlib
import json
import os
import re
import signal
import time
from .targets import atomic


def case_identity(case):
    """Tie startup evidence to the exact command, entry and accepted source bindings."""
    data={'name':case.name,'runner':case.runner.model_dump() if case.runner else None,
          'bindings':[b.model_dump() for b in case.bindings]}
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()


def startup_error(text):
    """Detect explicit fatal/error diagnostics, ignoring zero-count UVM summaries."""
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
    for line in text.splitlines():
        if re.search(r'\bUVM_(ERROR|FATAL)\s*:\s*0\s*$',line):
            continue
        if re.search(r'\bUVM_(ERROR|FATAL)\b|Traceback \(most recent call last\)|^\s*(?:\*\*\s*)?(?:ERROR:|Error-|Error:|error:|FATAL:|Fatal:)',line):
            return line[:500]
    return ''


async def terminate(process):
    """Stop the entire command group and reap the main process."""
    try:
        os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(),3)
    except asyncio.TimeoutError:
        os.killpg(process.pid,signal.SIGKILL)
        await process.wait()
    try:
        os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:
        pass


async def execute_smoke(runner,folder,row):
    """Watch readiness and error output, then stop without collecting coverage."""
    path=folder/(row['id']+'.json')
    log_path=folder/(row['id']+'.log')
    process=None
    ready_at=None
    try:
        with log_path.open('ab',buffering=0) as log:
            process=await asyncio.create_subprocess_exec('/bin/bash','-lc',row['command'],cwd=row['cwd'],
                env=dict(os.environ,PYTHONUNBUFFERED='1',TERM='dumb',FLOWCANVAS_RUN_DIR=row['run_dir']),
                stdout=log,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
            runner.processes[row['id']]=process
            row['status']='running';atomic(path,row)
            deadline=time.monotonic()+row['startup_timeout']
            while True:
                text=log_path.read_text(errors='replace')
                error=startup_error(text)
                if error:
                    raise ValueError(error)
                if ready_at is None and (match:=re.search(row['ready_pattern'],text)):
                    ready_at=time.monotonic()
                    row['startup_evidence']=text[max(0,match.start()-100):match.end()+200]
                if process.returncode is not None:
                    row['exit_code']=process.returncode
                    if process.returncode!=0:
                        raise ValueError('Command exited with code '+str(process.returncode))
                    if ready_at is None:
                        raise ValueError('Command ended without the required test-start evidence')
                    row['stop_reason']='natural_exit'
                    break
                if ready_at is not None and time.monotonic()-ready_at>=1:
                    row['stop_reason']='startup_confirmed'
                    await terminate(process)
                    row['exit_code']=process.returncode
                    break
                if time.monotonic()>=deadline:
                    raise ValueError('Timed out before confirming test startup')
                await asyncio.sleep(.1)
            # Include final diagnostics emitted during shutdown.
            error=startup_error(log_path.read_text(errors='replace'))
            if error:raise ValueError(error)
            row.update(status='interrupted' if row['stop_reason']=='startup_confirmed' else 'completed',
                       smoke_status='passed')
    except asyncio.CancelledError:
        row.update(status='interrupted',smoke_status='cancelled',stop_reason='user_stop')
    except Exception as exc:
        row.update(status='failed',smoke_status='failed',error=str(exc))
    finally:
        if process is not None:
            await terminate(process)
            if row.get('exit_code') is None:
                row['exit_code']=process.returncode
        row['ended']=time.time()
        atomic(path,row)
        runner.processes.pop(row['id'],None)
