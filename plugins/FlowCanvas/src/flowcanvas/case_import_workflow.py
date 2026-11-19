"""Validate imported cards and one complete, isolated coverage-smoke receipt."""
import json
from pathlib import Path

from ucagent.checkers.base import Checker
from .import_absence import skipped_import, absent_coverage
from .smoke_runs import validate_coverage_smoke_receipt
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
            skipped=skipped_import(scope,request,store,'testcase')
            if skipped is not None:return True,skipped
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
            snapshots={row['id']:row for row in request.get('testcase_snapshot',[])}
            if set(snapshots)-set(cards):
                raise ValueError('Synchronization must preserve existing cases and historical runs; report missing source instead of deleting cases')
            repairs=scope.get('repairs',[])
            if not isinstance(repairs,list) or any(not isinstance(row,dict) or row.get('id') not in snapshots or
                    not isinstance(row.get('reason'),str) or not row['reason'].strip() for row in repairs):
                raise ValueError('repairs must identify an existing case and a concrete source/configuration reason')
            repair_ids={row['id'] for row in repairs}
            fields=('name','title','description','timeline','overview','runner','bindings')
            for identity,before in snapshots.items():
                after=cards[identity].model_dump()
                if any(before.get(key)!=after.get(key) for key in fields) and identity not in repair_ids:
                    raise ValueError('Preserve existing case content unless a concrete repair is recorded: '+identity)
            coverage_skip=absent_coverage(scope,store)
            if self.mode=='coverage' and coverage_skip is None:
                config=data.coverage
                if config is None or config.mode!='shared':
                    raise ValueError('Configure shared coverage through test_case_runs before startup checks')
                if config.provider=='vcs':
                    from .native_vcs import configuration, build_identity
                    configuration(store.root)
                    build_identity(store.root)
                    if not (store.root/'output/flowcanvas/coverage/targets.json').is_file():
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
                if self.mode!='cards' and '{wave_dir}' not in card.runner.command:
                    raise ValueError('Runner must enable VCD dumping into {wave_dir}/waves.vcd: '+identity)
                if self.mode=='coverage' and coverage_skip is None and not all(token in card.runner.command for token in ('{coverage_db}','{coverage_name}')):
                    raise ValueError('Runner must forward shared coverage path and unique run name: '+identity)
            if self.mode in {'coverage','smoke'} and coverage_skip is not None:
                return True,{'cards':len(ids),'coverage_status':'skipped','reason':coverage_skip,'coverage_smoke_checked':False}
            if self.mode=='smoke':
                identity=scope.get('smoke_case_id')
                reference=scope.get('smoke_reference_case_id')
                if identity not in ids or reference not in ids or identity==reference:
                    raise ValueError('Record a dedicated smoke_case_id and an imported smoke_reference_case_id')
                validate_coverage_smoke_receipt(store.root,cards[identity],cards[reference],data.coverage,
                                                scope.get('smoke_run_id',''))
            return True,{'cards':len(ids),'coverage_smoke_checked':self.mode=='smoke'}
        except (OSError,ValueError,KeyError,TypeError) as exc:
            return False,{'error_code':'CASE_IMPORT_INCOMPLETE','error':str(exc),
                          'next_action':'Inspect the original cards and logs, repair configuration, and rerun only failed or outdated startup checks.'}
