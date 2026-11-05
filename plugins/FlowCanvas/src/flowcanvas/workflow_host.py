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
    overrides = {'backend.key_name': 'blank', 'template_overwrite.RTL_FILE': spec['rtl']}
    if spec.get('report_file'):
        overrides.update({'template_overwrite.PROJECT_ROOT': spec['project_root'],
                          'template_overwrite.SUMMARY_REPORT': spec['report_file']})
    if spec.get('dut_output'):
        overrides.update({'template_overwrite.PROJECT_ROOT': spec['project_root'],
                          'template_overwrite.SOURCE_RTL': spec['source_file'],
                          'template_overwrite.DUT_OUTPUT': spec['dut_output']})
    agent = VerifyAgent(str(root / 'workspace'), spec['dut'], 'summary',
                        config_file=spec['config'], cfg_override=overrides,
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
        if stages and all(stage['completed'] for stage in stages) and not (root / 'complete.json').exists():
            # Publish accepted output before the completion receipt, so UI completion
            # always implies that the project artifact is available.
            receipt = {'completed': True}
            if spec.get('report_file'):
                report = Path(spec['report_file'])
                project = Path(spec['project_root']).resolve()
                if not report.resolve().is_relative_to(project):
                    raise ValueError('Summary output must remain inside the bound project')
                report.parent.mkdir(parents=True, exist_ok=True)
                temporary_report = report.with_suffix('.tmp')
                with temporary_report.open('x', encoding='utf-8') as handle:
                    handle.write((root / 'workspace/summary/summary.md').read_text(encoding='utf-8'))
                temporary_report.replace(report)
                receipt['report_file'] = str(report)
            (root / 'complete.json').write_text(json.dumps(receipt))
    try:
        ok, message = agent.pdb.start_mcp_server('127.0.0.1', spec['port'], no_file_ops=False)
        if not ok:
            raise RuntimeError(message)
        (root / 'ready.json').write_text(json.dumps({'url': agent.pdb._mcp_server.url(), 'instruction': agent.cfg.mission.prompt.system}))
        while not agent.is_exit() and not stopping:
            snapshot()
            time.sleep(1)
    finally:
        snapshot()
        agent.stage_manager.save_stage_info()
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
