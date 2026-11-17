"""Project import composes the existing synchronization policies and real workflow host."""
import json
from pathlib import Path
import pytest
from ucagent.util.config import load_yaml_with_env_vars
from flowcanvas.project_import_workflow import project_import_overrides
from flowcanvas.plugin import get_plugin
from flowcanvas.chat import ChatService
from flowcanvas.workflow_runtime import WorkflowRuntime


def test_composition_reuses_both_workflows_in_order():
    """Changes to either child workflow automatically reach the combined workflow unchanged."""
    folder=Path(__file__).parents[1]/'src/flowcanvas/workflows'
    targets=load_yaml_with_env_vars(folder/'import-targets.yaml')
    cases=load_yaml_with_env_vars(folder/'import-testcases.yaml')
    combined=project_import_overrides()
    assert combined['stage']==targets['stage']+cases['stage']
    assert targets['mission']['prompt']['system'] in combined['mission.prompt.system']
    assert cases['mission']['prompt']['system'] in combined['mission.prompt.system']
    assert 'ALL stages of BOTH phases' in combined['mission.prompt.system']
    assert next(w for w in get_plugin().workflows if w.name=='import-project').config_file.is_file()


@pytest.mark.asyncio
async def test_project_import_host_preserves_both_baselines(tmp_path):
    """Start one native MCP host without a model, compilation, simulator or child workflows."""
    project=tmp_path/'project';project.mkdir()
    rtl=project/'Probe.v';rtl.write_text('module Probe; endmodule\n')
    service=ChatService(tmp_path/'state');service.projects.state['current']={'path':str(project)}
    owner=service.create();owner['status']='running';runtime=WorkflowRuntime(service,owner)
    try:
        result=await runtime.run('import-project',str(rtl),'Probe')
        card=result['card'];request=json.loads((Path(card['root'])/'request.json').read_text())
        assert request['project_root']==str(project.resolve())
        assert request['target_baseline']==request['testcase_baseline']==[]
        assert request['target_snapshot']==request['testcase_snapshot']==[]
        assert card['current_stage']=='inspect_verification_scope'
        stages=[stage for stage in card['stages'] if stage.get('kind')!='startup']
        assert [stage['name'] for stage in stages if not stage.get('parent')]==[
            'inspect_verification_scope','import_verification_cards','reconcile_import',
            'inspect_testcase_scope','import_testcases','validate_coverage_smoke','reconcile_testcase_import']
        assert result['tools']
        assert not (project/'verification').exists()
    finally:
        await runtime.close()
        await service.close()
