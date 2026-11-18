"""Accept explicit absence evidence without fabricating import cards or run receipts."""
import re
from pathlib import Path
from .line_bindings import read_source


def skipped_import(scope, request, store, prefix):
    """Require inspected source, a concrete reason, and unchanged existing cards for a skip."""
    if scope.get('status') != 'skipped':
        return None
    reason=scope.get('skip_reason')
    if not isinstance(reason,str) or not reason.strip():
        raise ValueError('Skipped synchronization requires a concrete skip_reason')
    if scope.get('files') != [] or scope.get('card_ids') != [] or scope.get('repairs',[]) != []:
        raise ValueError('Skipped synchronization must have empty files, card_ids and repairs')
    inspected=scope.get('inspected_files')
    if not isinstance(inspected,list) or not inspected:
        raise ValueError('Skipped synchronization requires inspected_files as source evidence')
    for file in inspected:
        if not isinstance(file,str) or Path(file).is_absolute():
            raise ValueError('Use existing project-relative inspected source files')
        path=store.file(file)
        if prefix=='target':
            text=re.sub(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*',' ',read_source(path))
            if re.search(r'\bcovergroup\b',text):
                raise ValueError('Coverage declarations exist; synchronize them rather than skipping')
    current={row.id:row.model_dump() for row in store.read().targets}
    before={row['id']:row for row in request.get(prefix+'_snapshot',[])}
    if set(current)!=set(request.get(prefix+'_baseline',[])) or current!=before:
        raise ValueError('Skipped synchronization must preserve all existing cards unchanged')
    return {'status':'skipped','reason':reason.strip(),'inspected_files':len(inspected)}


def absent_coverage(scope, store):
    """Allow importing runnable cases without inventing a missing functional coverage model."""
    if scope.get('coverage_status')!='skipped':
        return None
    reason=scope.get('coverage_skip_reason')
    files=scope.get('coverage_inspected_files')
    if not isinstance(reason,str) or not reason.strip() or not isinstance(files,list) or not files:
        raise ValueError('Coverage skip requires coverage_skip_reason and coverage_inspected_files')
    from .targets import Targets
    if Targets(store.root).read().targets:
        raise ValueError('Existing coverage targets require normal configuration and smoke acceptance')
    for file in files:
        if not isinstance(file,str) or Path(file).is_absolute():
            raise ValueError('Use existing project-relative coverage inspection files')
        text=re.sub(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*',' ',read_source(store.file(file)))
        if re.search(r'\bcovergroup\b',text):
            raise ValueError('Coverage declarations exist; missing configuration is not an absent coverage model')
    return reason.strip()
