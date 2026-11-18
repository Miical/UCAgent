"""Compose project import from the live target and test-case synchronization definitions."""
from copy import deepcopy
from pathlib import Path
from ucagent.util.config import load_yaml_with_env_vars


def project_import_overrides():
    """Reuse both source workflows in order, including their original nested acceptance gates."""
    folder=Path(__file__).parent/'workflows'
    project=load_yaml_with_env_vars(folder/'import-project.yaml')
    targets=load_yaml_with_env_vars(folder/'import-targets.yaml')
    cases=load_yaml_with_env_vars(folder/'import-testcases.yaml')
    policy=project['mission']['prompt']['system']
    prompts=[workflow['mission']['prompt']['system'] for workflow in (targets,cases)]
    phases = [
        {'name': name, 'desc': label, 'task': [], 'stage': deepcopy(workflow['stage'])}
        for name, label, workflow in [
            ('sync_verification_targets', 'Sync Verification Targets', targets),
            ('sync_test_cases', 'Sync Test Cases', cases),
        ]
    ]
    return {'stage': phases, 'mission.prompt.system': '\n\n'.join([policy, *prompts])}


def show_workflow_phases(phases, stages):
    """Expose group-only parents while keeping the engine's executable indices intact."""
    by_name = {stage['name']: stage for stage in stages}
    display = []
    for phase in phases:
        children = [by_name[stage.name] for stage in phase.get_substages()]
        display.append({
            'name': phase.name, 'description': phase.description(), 'kind': 'group',
            'depth': 0, 'parent': None,
            'completed': all(stage['completed'] or stage['skipped'] for stage in children),
            'skipped': all(stage['skipped'] for stage in children),
        })
        display.extend(children)
    grouped = {stage['name'] for stage in display}
    return display + [stage for stage in stages if stage['name'] not in grouped]
