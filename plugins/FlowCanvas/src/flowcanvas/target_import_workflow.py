"""Read-only acceptance of AI-selected import scopes and explicit line bindings."""
import json
import re
from pathlib import Path

from ucagent.checkers.base import Checker
from .targets import Targets
from .line_bindings import read_source


class TargetImportChecker(Checker):
    """Check real input files and finished card ranges without parsing UVM syntax."""

    def __init__(self, final=False, **kwargs):
        """Select source-scope or final card acceptance."""
        super().__init__()
        self.final=final

    def do_check(self, **kwargs):
        """Validate paths, explicit exclusions, card ownership and source range integrity."""
        try:
            work=Path(self.workspace)
            request=json.loads((work.parent/'request.json').read_text())
            store=Targets(request['project_root'])
            scope=json.loads((work/'summary/import-scope.json').read_text())
            files=scope.get('files')
            if not isinstance(files,list) or not files or any(not isinstance(f,str) for f in files):
                raise ValueError('files must list project-relative verification source paths')
            for file in files:
                if Path(file).is_absolute():
                    raise ValueError('Use project-relative source paths')
                store.file(file)
            for file in scope.get('documents',[]):
                path=(store.root/file).resolve()
                if Path(file).is_absolute() or not path.is_relative_to(store.root) or not path.is_file():
                    raise ValueError('Document must be an existing project-relative file')
            if not self.final:
                return True, {'files':len(files)}
            ids=scope.get('card_ids')
            if not isinstance(ids,list) or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
                raise ValueError('card_ids must list the unique imported or reused card IDs')
            report=store.status()
            cards={t.id:t for t in store.read().targets}
            baseline=set(request.get('target_baseline',[]))
            if set(cards)-baseline-set(ids):
                raise ValueError('Include every card allocated by this import in card_ids or remove unused drafts')
            snapshots={row['id']:row for row in request.get('target_snapshot',[])}
            if set(snapshots)-set(cards):
                raise ValueError('Synchronization must preserve existing targets; report removed source instead of deleting targets')
            repairs=scope.get('repairs',[])
            if not isinstance(repairs,list) or any(not isinstance(row,dict) or row.get('id') not in snapshots or
                    not isinstance(row.get('reason'),str) or not row['reason'].strip() for row in repairs):
                raise ValueError('repairs must identify an existing target and a concrete source-backed reason')
            repair_ids={row['id'] for row in repairs}
            fields=('kind','parent','name','description','explanation','bindings')
            for identity,before in snapshots.items():
                after=cards[identity].model_dump()
                if any(before.get(key)!=after.get(key) for key in fields) and identity not in repair_ids:
                    raise ValueError('Preserve existing target content unless a concrete repair is recorded: '+identity)
            for identity in ids:
                card=cards.get(identity)
                if card is None or card.kind is None or card.lifecycle!='ready':
                    raise ValueError('Finish the imported card: '+identity)
                if not card.name.strip() or not card.description.strip():
                    raise ValueError('Card needs a readable title and description: '+identity)
                if report['reports'][identity]['status']!='bound':
                    raise ValueError('Card requires valid marker and line-count bindings: '+identity)
                if not any(b.file in files for b in card.bindings):
                    raise ValueError('Bind the card within the declared source scope: '+identity)
                # Validate the selected declaration boundary without parsing or elaborating UVM.
                patterns={
                    'group':r'^covergroup\b',
                    'point':r'^(?:[A-Za-z_$][\w$]*\s*:\s*)?(?:coverpoint|cross)\b',
                    'testpoint':r'^(?:wildcard\s+)?bins\s+[A-Za-z_$][\w$]*\s*(?:\[[^\]]*\]\s*)?=',
                }
                declarations=[]
                for binding in report['reports'][identity]['bindings']:
                    if binding['role']!='definition' or binding['file'] not in files:
                        continue
                    text=''.join(read_source(store.file(binding['file'])).splitlines(keepends=True)[binding['line']-1:binding['end_line']])
                    text=re.sub(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*', ' ',text).lstrip()
                    declarations.append(text)
                if not any(re.match(patterns[card.kind],text) for text in declarations):
                    raise ValueError('Import only covergroup/group, coverpoint or cross/point, and normal bins/testpoint; bind the declaration with role=definition: '+identity)
                if card.parent and card.parent not in ids:
                    raise ValueError('Include the imported hierarchy parent in card_ids: '+identity)
            coverage_sources=[]
            for file in files:
                text=re.sub(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*', ' ',read_source(store.file(file)))
                if re.search(r'\bcovergroup\b',text):coverage_sources.append(file)
            if not ids and coverage_sources:
                raise ValueError('Coverage declarations exist: split shared definitions into independently wired copies and import them; empty synchronization cannot pass')
            for item in scope.get('exclusions',[]):
                if not isinstance(item,dict) or item.get('file') not in files or not isinstance(item.get('reason'),str) or not item['reason'].strip():
                    raise ValueError('Every exclusion requires a scoped file and concrete reason')
                if item['file'] in coverage_sources and re.search(r'reus|shared|multiple|multi.instance|duplicat|复用|共用|多实例|共享|重复',item['reason'],re.I):
                    raise ValueError('Shared coverage cannot be excluded; copy and wire independent per-instance definitions before importing: '+item['file'])
            return True, {'cards':len(ids),'revision':report['revision']}
        except (OSError,ValueError,KeyError,TypeError) as exc:
            return False, {'error_code':'TARGET_IMPORT_INCOMPLETE','error':str(exc),
                           'next_action':'Inspect the files and original cards, correct their ranges or import-scope.json, then retry.'}
