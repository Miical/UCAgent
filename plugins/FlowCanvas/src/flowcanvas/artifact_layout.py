"""Move legacy project artifacts into the shared output tree while preserving old links."""
from pathlib import Path
import json
import threading

_LOCK = threading.RLock()


def migrate_artifacts(root):
    """Relocate idle legacy directories without overwriting existing output or breaking history paths."""
    root=Path(root).resolve()
    with _LOCK:
        legacy=root/'.flowcanvas'
        pending=[name for name in ('test-runs','coverage','runs','backups')
                 if (legacy/name).is_dir() and not (legacy/name).is_symlink()]
        if not pending:return
        for file in (legacy/'test-runs').glob('*.json'):
            if json.loads(file.read_text()).get('status') in {'starting','running'}:
                raise ValueError('Wait for active project runs before migrating FlowCanvas outputs')
        output=root/'output/flowcanvas'
        if not output.resolve().is_relative_to(root):raise ValueError('FlowCanvas output escapes project')
        for name in pending:
            if (output/name).exists():raise ValueError('Both legacy and new artifact directories exist: '+name)
        output.mkdir(parents=True,exist_ok=True)
        for name in pending:
            (legacy/name).rename(output/name)
            # Historical commands and receipts contain absolute legacy paths.
            (legacy/name).symlink_to(Path('../output/flowcanvas')/name,target_is_directory=True)
