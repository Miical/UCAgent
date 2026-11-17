"""Project artifacts move without losing historical paths or interrupting active runs."""
import json
import pytest
from flowcanvas.artifact_layout import migrate_artifacts


def test_legacy_outputs_move_and_keep_aliases(tmp_path):
    """Preserve exact run bytes and make repeat activation harmless."""
    legacy=tmp_path/'.flowcanvas/test-runs';legacy.mkdir(parents=True)
    (legacy/'run.json').write_text('{"status":"completed"}')
    migrate_artifacts(tmp_path);migrate_artifacts(tmp_path)
    assert legacy.is_symlink()
    assert (tmp_path/'output/flowcanvas/test-runs/run.json').read_bytes()==(legacy/'run.json').read_bytes()


def test_active_and_conflicting_artifacts_remain_untouched(tmp_path):
    """Never overwrite another output directory or move an executing command's files."""
    legacy=tmp_path/'.flowcanvas/test-runs';legacy.mkdir(parents=True)
    record=legacy/'run.json';record.write_text(json.dumps(dict(status='running')))
    with pytest.raises(ValueError,match='active'):migrate_artifacts(tmp_path)
    assert not legacy.is_symlink()
    record.write_text(json.dumps(dict(status='completed')))
    (tmp_path/'output/flowcanvas/test-runs').mkdir(parents=True)
    with pytest.raises(ValueError,match='Both'):migrate_artifacts(tmp_path)
    assert record.is_file() and not legacy.is_symlink()
