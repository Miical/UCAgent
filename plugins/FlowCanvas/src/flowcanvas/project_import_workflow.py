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
    return {'stage':deepcopy(targets['stage']+cases['stage']),
            'mission.prompt.system':policy+'\n\n'+prompts[0]+'\n\n'+prompts[1]}
