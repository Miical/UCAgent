"""Host a workflow's UCAgent MCP service in an isolated task process, without a model."""
import json
from pathlib import Path
import sys
import signal
import time
from ucagent.verify_agent import VerifyAgent


def main():
    """Publish readiness and completion receipts owned by UCAgent rather than the model."""
    root = Path(sys.argv[1])
    spec = json.loads((root / 'request.json').read_text())
    agent = VerifyAgent(str(root / 'workspace'), spec['dut'], 'summary',
                        config_file=spec['config'],
                        cfg_override={'backend.key_name': 'blank',
                                      'template_overwrite.RTL_FILE': spec['rtl']},
                        no_history=False, no_embed_tools=True, exit_on_completion=False)
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    def snapshot():
        task_data = agent.pdb.api_task_list()
        rows = task_data.get('task_list', {}).get('stage_list', [])
        stages = []
        for index, stage in enumerate(agent.stage_manager.stages):
            row = rows[index] if index < len(rows) else {}
            stages.append({'name': stage.name, 'completed': stage.is_completed(),
                           'description': stage.description(), 'index': index,
                           'fail_count': stage.fail_count, 'check_pass': stage.check_pass,
                           'skipped': stage.is_skipped(), 'human_check': stage.is_hmcheck_needed(),
                           'time_cost': row.get('time_cost'), 'skills': row.get('skill_list', [])})
        stats = agent.status_info()
        tui = {'mission': task_data.get('mission_name'),
               'changed_files': [{'mtime': mtime, 'file': filename} for _, mtime, filename in agent.pdb.api_changed_files()[:5]],
               # TUI is_hot includes recently finished calls; only is_busy means executing.
               'tools': [{'name': tool.name, 'count': tool.call_count,
                          'busy': tool.is_busy() and not all(stage['completed'] for stage in stages)}
                         for tool in agent.test_tools],
               'runtime': {key: stats[key] for key in ('Run Time', 'Idle', 'Interaction Mode') if key in stats}}
        current = agent.stage_manager.get_current_stage()
        temporary = root / 'progress.tmp'
        temporary.write_text(json.dumps({'stages': stages, 'current_stage': current.name if current else '', 'tui': tui}))
        temporary.replace(root / 'progress.json')
        if stages and all(stage['completed'] for stage in stages):
            (root / 'complete.json').write_text(json.dumps({'completed': True}))
    try:
        ok, message = agent.pdb.start_mcp_server('127.0.0.1', spec['port'], no_file_ops=False)
        if not ok:
            raise RuntimeError(message)
        (root / 'ready.json').write_text(json.dumps({'url': agent.pdb._mcp_server.url(), 'instruction': agent.cfg.mission.prompt.system}))
        while not agent.is_exit() and not stopping:
            snapshot()
            complete = all(stage.is_completed() for stage in agent.stage_manager.stages)
            if complete:
                (root / 'complete.json').write_text(json.dumps({'completed': True}))
            time.sleep(1)
    finally:
        snapshot()
        agent.stage_manager.save_stage_info()
        if all(stage.is_completed() for stage in agent.stage_manager.stages):
            (root / 'complete.json').write_text(json.dumps({'completed': True}))
        agent.exit()
        # Exit sets the agent flag inside an MCP call. Keep the process alive
        # until that response drains; the daemon server thread cannot outlive main.
        server = getattr(agent.pdb, '_mcp_server', None)
        if server and server.is_running:
            stopped, message = server.stop(timeout=60)
            if not stopped:
                raise RuntimeError(message)


if __name__ == '__main__':
    main()
