"""Validate the one primary, run-owned VCD used by the waveform viewer."""
import hashlib
from pathlib import Path

from ucagent.util.waveform_formats import open_waveform_reader


def validate_run_waveform(root, run):
    """Require a readable signal trace with a positive time span and return its byte receipt."""
    folder=(Path(root)/'output/flowcanvas/test-runs').resolve()
    directory=(folder/run['id']).resolve()
    path=directory/'waveforms/waves.vcd'
    if not directory.is_relative_to(folder) or not path.resolve().is_relative_to(directory):
        raise ValueError('Waveform escapes its managed run')
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError('Missing primary waveform: {wave_dir}/waves.vcd; enable simulator VCD dumping')
    before=path.stat()
    with open_waveform_reader(path) as reader:
        page=reader.get_matched_signals('**',limit=1)
        if not page['total'] or not page['signals'] or reader.end_time<=reader.begin_time:
            raise ValueError('VCD requires declared signals and a positive simulation time span')
        signal=next(iter(page['signals'].values()))
        events=reader.load_value_changes(signal,dict(zip('01xz',(0,1,0,0))),
                                         begin_time=reader.begin_time,end_time=reader.end_time+1)['mapped']
        if not len(events):raise ValueError('VCD has no readable signal values')
        result=dict(path='waveforms/waves.vcd',begin=str(reader.begin_time),end=str(reader.end_time),signals=page['total'])
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    after=path.stat()
    if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
        raise ValueError('Waveform is still changing')
    return {**result,'size':after.st_size,'sha256':digest.hexdigest()}
