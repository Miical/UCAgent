"""Validate imported cards and one complete, isolated coverage-smoke receipt."""
import json
import re
from pathlib import Path

from ucagent.checkers.base import Checker
from .smoke_runs import case_identity, coverage_identity, startup_error, validate_smoke_report
from .testcases import Cases


class CaseImportChecker(Checker):
    """Check import scope, runnable card definitions, and current startup receipts."""

    def __init__(self, mode='scope', **kwargs):
        """Select scope, card, runner, coverage or smoke acceptance without launching commands."""
        super().__init__()
        if mode not in {'scope','cards','runners','coverage','smoke'}:
            raise ValueError('Unknown case import gate')
        self.mode=mode

    def do_check(self, **kwargs):
        """Read stored evidence and reject missing, failed or outdated startup checks."""
        try:
            work=Path(self.workspace)
            request=json.loads((work.parent/'request.json').read_text())
            store=Cases(request['project_root'])
            scope=json.loads((work/'summary/case-import.json').read_text())
            files=scope.get('files')
            if not isinstance(files,list) or not files:
                raise ValueError('List the project-relative test implementation files')
            for file in files:
                if Path(file).is_absolute():raise ValueError('Use project-relative test files')
                store.file(file)
            if self.mode=='scope':return True,{'files':len(files)}
            ids=scope.get('card_ids')
            if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):
                raise ValueError('List every imported or reused card ID')
            data=store.read();cards={c.id:c for c in data.targets};reports=store.status()['reports']
            if set(cards)-set(request.get('testcase_baseline',[]))-set(ids):
                raise ValueError('Account for all newly allocated cards or remove unused drafts')
            if self.mode=='coverage':
                config=data.coverage
                if config is None or config.mode!='shared':
                    raise ValueError('Configure shared coverage through test_case_runs before startup checks')
                if config.provider=='vcs':
                    from .native_vcs import configuration, build_identity
                    configuration(store.root)
                    build_identity(store.root)
                    if not (store.root/'.flowcanvas/coverage/targets.json').is_file():
                        raise ValueError('Prepare native functional card mappings in coverage/targets.json')
                else:
                    adapter=scope.get('coverage_adapter')
                    if not isinstance(adapter,str) or Path(adapter).is_absolute():
                        raise ValueError('Record the project-relative coverage_adapter source path')
                    store.file(adapter)
            entries=set()
            for identity in ids:
                card=cards.get(identity)
                if (card is None or reports[identity]['status']!='bound' or
                        card.lifecycle not in ({'ready','creating','updating'} if self.mode=='cards' else {'ready'})):
                    raise ValueError('Finish a source-backed card: '+str(identity))
                store.validate_card(card, require_runner=self.mode!='cards')
                definitions=[b for b in card.bindings if b.role=='definition']
                if not any(b.file in files for b in definitions):
                    raise ValueError('Bind the case implementation within the selected files')
                entry=(card.name,card.runner.command if card.runner else '')
                if entry in entries:raise ValueError('Duplicate case entry and command')
                entries.add(entry)
                if self.mode=='coverage' and not all(token in card.runner.command for token in ('{coverage_db}','{coverage_name}')):
                    raise ValueError('Runner must forward shared coverage path and unique run name: '+identity)
            if self.mode=='smoke':
                identity=scope.get('smoke_case_id')
                reference=scope.get('smoke_reference_case_id')
                if identity not in ids or reference not in ids or identity==reference:
                    raise ValueError('Record a dedicated smoke_case_id and an imported smoke_reference_case_id')
                card,reference_card=cards[identity],cards[reference]
                if card.runner!=reference_card.runner:
                    raise ValueError('Smoke must use the same runner/build path as its reference case')
                run_id=scope.get('smoke_run_id','')
                if not isinstance(run_id,str) or not re.fullmatch('[0-9a-f]{32}',run_id):
                    raise ValueError('Record the completed coverage smoke run ID')
                folder=store.root/'.flowcanvas/test-runs'
                run=json.loads((folder/(run_id+'.json')).read_text())
                if (run.get('mode')!='coverage_smoke' or run.get('case_id')!=identity or
                        run.get('smoke_status')!='passed' or run.get('status')!='completed' or
                        run.get('exit_code')!=0 or run.get('stop_reason')!='natural_exit' or
                        run.get('coverage_status')!='validated' or not run.get('ended')):
                    raise ValueError('Complete the short smoke naturally and validate real coverage extraction')
                if run.get('case_identity')!=case_identity(card):
                    raise ValueError('Smoke command or source changed; repeat coverage smoke')
                if data.coverage is None or run.get('coverage_identity')!=coverage_identity(data.coverage.model_dump()):
                    raise ValueError('Coverage configuration changed; repeat coverage smoke')
                if data.coverage.provider=='vcs':
                    from .native_vcs import metadata_identity
                    if run.get('native_coverage_identity')!=metadata_identity(store.root):
                        raise ValueError('Native build or coverage mappings changed; repeat coverage smoke')
                database=Path(run['coverage_db']).resolve()
                run_dir=Path(run['run_dir']).resolve()
                if not database.is_relative_to(run_dir) or not run_dir.is_relative_to(folder):
                    raise ValueError('Smoke database must be isolated inside its managed run directory')
                artifact=Path(run.get('coverage_artifact','')).resolve()
                if not artifact.is_relative_to(run_dir):
                    raise ValueError('Smoke coverage artifact must belong to the managed run')
                report=validate_smoke_report(store.root,json.loads(artifact.read_text()))
                if report['build_id']!=run.get('build_id'):
                    raise ValueError('Smoke coverage build does not match the executed build')
                log=(folder/(run_id+'.log')).read_text(errors='replace')
                if startup_error(log) or not re.search(run['ready_pattern'],log):
                    raise ValueError('Smoke log lacks valid execution evidence')
            return True,{'cards':len(ids),'coverage_smoke_checked':self.mode=='smoke'}
        except (OSError,ValueError,KeyError,TypeError) as exc:
            return False,{'error_code':'CASE_IMPORT_INCOMPLETE','error':str(exc),
                          'next_action':'Inspect the original cards and logs, repair configuration, and rerun only failed or outdated startup checks.'}
