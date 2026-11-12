"""Validate imported case cards and managed startup-smoke evidence."""
import json
import re
from pathlib import Path

from ucagent.checkers.base import Checker
from .smoke_runs import case_identity, startup_error
from .testcases import Cases


class CaseImportChecker(Checker):
    """Check import scope, runnable card definitions, and current startup receipts."""

    def __init__(self, mode='scope', **kwargs):
        """Select scope, card or smoke acceptance without launching commands."""
        super().__init__()
        if mode not in {'scope','cards','smoke'}:
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
            entries=set()
            for identity in ids:
                card=cards.get(identity)
                if card is None or card.lifecycle!='ready' or reports[identity]['status']!='bound':
                    raise ValueError('Finish a source-backed card: '+str(identity))
                store.validate_card(card)
                definitions=[b for b in card.bindings if b.role=='definition']
                if not any(b.file in files for b in definitions):
                    raise ValueError('Bind the case implementation within the selected files')
                entry=(card.name,card.runner.command)
                if entry in entries:raise ValueError('Duplicate case entry and command')
                entries.add(entry)
                if self.mode!='smoke':continue
                run_id=scope.get('smoke_runs',{}).get(identity,'')
                if not isinstance(run_id,str) or not re.fullmatch('[0-9a-f]{32}',run_id):
                    raise ValueError('Record the managed startup smoke run ID: '+identity)
                folder=store.root/'.flowcanvas/test-runs'
                run=json.loads((folder/(run_id+'.json')).read_text())
                if (run.get('mode')!='startup_smoke' or run.get('case_id')!=identity or
                        run.get('smoke_status')!='passed' or not run.get('ended')):
                    raise ValueError('Startup smoke did not pass: '+identity)
                if run.get('case_identity')!=case_identity(card):
                    raise ValueError('Command or source binding changed; repeat startup smoke: '+identity)
                log=(folder/(run_id+'.log')).read_text(errors='replace')
                if startup_error(log) or not re.search(run['ready_pattern'],log):
                    raise ValueError('Startup log lacks valid execution evidence: '+identity)
            return True,{'cards':len(ids),'startup_checked':self.mode=='smoke'}
        except (OSError,ValueError,KeyError,TypeError) as exc:
            return False,{'error_code':'CASE_IMPORT_INCOMPLETE','error':str(exc),
                          'next_action':'Inspect the original cards and logs, repair configuration, and rerun only failed or outdated startup checks.'}
