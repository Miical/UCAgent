"""Run-scoped waveform inspection and persistent, source-checked AI annotations."""
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ucagent.util.waveform_formats import open_waveform_reader
from .targets import atomic

_LOCK = threading.RLock()
_HASHES = {}
_BOUNDS = {}


class WavePoint(BaseModel):
    """One explained native-tick position with explicit observed signals."""
    model_config = ConfigDict(extra='forbid')
    time: str = Field(pattern=r'^(0|[1-9][0-9]*)$')
    start: str | None = Field(default=None, pattern=r'^(0|[1-9][0-9]*)$')
    end: str | None = Field(default=None, pattern=r'^(0|[1-9][0-9]*)$')
    title: str = Field(min_length=1, max_length=120)
    explanation: str = Field(min_length=1, max_length=2000)
    confidence: str = Field(default='inferred', pattern=r'^(observed|inferred)$')
    signals: list[str] = Field(min_length=1, max_length=8)


class WaveAnalysis(BaseModel):
    """Selected real signals and ordered points; explanations do not certify a test."""
    model_config = ConfigDict(extra='forbid')
    signals: list[str] = Field(min_length=1, max_length=32)
    points: list[WavePoint] = Field(min_length=1, max_length=16)


class BugWaveAnalysis(WaveAnalysis):
    """Summarize this run's evidence about the linked Bug, then anchor it to the trace."""
    verdict: Literal['reproduced', 'not_reproduced', 'insufficient_evidence']
    summary: str = Field(min_length=1, max_length=700, description='What the trace shows about the Bug, including the expected versus actual behavior or the missing evidence')
    signals: list[str] = Field(max_length=32)
    points: list[WavePoint] = Field(max_length=16)

    @model_validator(mode='after')
    def validate_evidence(self):
        """Tie conclusions to visible points and permit an explicit evidence gap."""
        if not self.points and self.verdict != 'insufficient_evidence':
            raise ValueError('A reproduction verdict requires at least one waveform evidence point')
        if set(self.signals) != {signal for point in self.points for signal in point.signals}:
            raise ValueError('Analysis signals must be the union of the evidence point signals')
        return self


class WaveAnalysisStore:
    """Constrain every inspection and annotation to an owned execution artifact."""
    def __init__(self, root, case_id, run_id, waveform):
        """Resolve the exact case/run/file and reject missing or escaping paths."""
        self.root = Path(root).resolve()
        if not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise ValueError('Invalid run ID')
        folder = (self.root/'output/flowcanvas/test-runs').resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError('Run storage escapes project')
        self.run = json.loads((folder/(run_id+'.json')).read_text())
        self.bug_id = case_id if case_id.startswith('bug_') else None
        if self.bug_id:
            from .bugs import Bugs
            bug = next((row for row in Bugs(self.root).read().targets
                        if row.id == self.bug_id and row.lifecycle != 'draft'), None)
            if bug is None or self.run.get('case_id') not in bug.case_ids:
                raise ValueError('Run does not belong to a test case linked to this Bug')
        elif self.run.get('case_id') != case_id:
            raise ValueError('Run does not belong to the selected case')
        directory = (folder/run_id).resolve()
        if not directory.is_relative_to(folder):
            raise ValueError('Run directory escapes storage')
        self.wave = (directory/waveform).resolve()
        if not self.wave.is_relative_to(directory) or self.wave.suffix.lower() not in {'.vcd', '.fst'} or not self.wave.is_file():
            raise ValueError('Select an existing VCD/FST artifact within this run')
        self.waveform = self.wave.relative_to(directory).as_posix()
        if self.bug_id:
            artifact = hashlib.sha256(self.waveform.encode()).hexdigest()
            self.file = directory / 'bug-waveform-analysis' / self.bug_id / (artifact + '.json')
            if self.file.resolve() != self.file:
                raise ValueError('Bug waveform analysis storage cannot use symlinks')
        else:
            self.file = directory / 'waveform-analysis.json'

    def fingerprint(self):
        """Bind explanations to exact waveform bytes rather than a reused filename."""
        stat = self.wave.stat()
        identity = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        with _LOCK:
            cached = _HASHES.get(self.wave)
            if cached and cached[0] == identity:
                return cached[1]
        digest = hashlib.sha256()
        with self.wave.open('rb') as stream:
            while chunk := stream.read(1024*1024):
                digest.update(chunk)
        after = self.wave.stat()
        if identity != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Waveform is changing; wait until writing finishes')
        result = digest.hexdigest()
        with _LOCK:
            if len(_HASHES) >= 128:
                _HASHES.clear()
            _HASHES[self.wave] = (identity, result)
        return result

    def read(self):
        """Expose stale annotations without applying them to changed waveform contents."""
        if not self.file.exists():
            return {'revision': 0, 'status': 'empty', 'analysis': None}
        saved = json.loads(self.file.read_text())
        valid = saved['waveform'] == self.waveform and saved['sha256'] == self.fingerprint()
        analysis = saved['analysis'] if valid else None
        if analysis is not None and 'begin' not in analysis:
            # Supply actual bounds for automatic zoom without rewriting stored interpretations.
            key = (self.wave, saved['sha256'])
            with _LOCK:
                bounds = _BOUNDS.get(key)
            if bounds is None:
                with open_waveform_reader(self.wave) as reader:
                    bounds = (str(reader.begin_time), str(reader.end_time))
                with _LOCK:
                    if len(_BOUNDS) >= 128:
                        _BOUNDS.clear()
                    _BOUNDS[key] = bounds
            analysis = {**analysis, 'begin': bounds[0], 'end': bounds[1]}
        return dict(revision=saved['revision'], status='ready' if valid else 'stale', analysis=analysis)

    def execute(self, operation='inspect', query='**', signals=None, start=None, end=None,
                offset=0, limit=100, analysis=None, expected_revision=0):
        """Inspect catalog/events or atomically save validated points, never running a test."""
        if operation == 'read':
            return self.read()
        if operation not in {'inspect', 'values', 'save'}:
            raise ValueError('Use inspect, values, read or save')
        if self.run.get('status') in {'starting', 'running'}:
            raise ValueError('Wait until this execution ends before analyzing its waveform')
        if not 0 <= offset or not 1 <= limit <= 100:
            raise ValueError('Use offset>=0 and limit=1..100')
        analysis_model = BugWaveAnalysis if self.bug_id else WaveAnalysis
        before = self.fingerprint()
        with open_waveform_reader(self.wave) as reader:
            if operation == 'inspect':
                page = reader.get_matched_signals(query, offset=offset, limit=limit)
                return dict(begin=str(reader.begin_time), end=str(reader.end_time), time_unit='native_ticks',
                            total=page['total'], offset=offset, signals=[dict(name=s.full_name, width=s.width) for s in page['signals'].values()],
                            saved=self.read(), schema=analysis_model.model_json_schema())
            selected = analysis_model.model_validate(analysis) if operation == 'save' else None
            names = selected.signals if selected else signals or []
            if (not names and selected is None) or len(names) > 32 or len(set(names)) != len(names):
                raise ValueError('Provide 1..32 unique exact signal names')
            descriptors = {}
            for name in names:
                found = reader.get_matched_signals(name, limit=100)['signals'].values()
                match = next((s for s in found if s.full_name == name), None)
                if match is None:
                    raise ValueError('Signal not found: '+name)
                descriptors[name] = match
            if operation == 'values':
                lo = reader.begin_time if start is None else int(start)
                hi = reader.end_time if end is None else int(end)
                if not reader.begin_time <= lo <= hi <= reader.end_time:
                    raise ValueError('Window must lie within the waveform native-tick bounds')
                rows = {name: self.events(reader, signal, lo, hi) for name, signal in descriptors.items()}
                return dict(time_unit='native_ticks', begin=str(lo), end=str(hi),
                            signals={name: dict(total=len(events), events=events[offset:offset+limit]) for name, events in rows.items()})
            points = []
            for point in selected.points:
                tick = int(point.time)
                if not reader.begin_time <= tick <= reader.end_time:
                    raise ValueError('Point is outside waveform time bounds')
                if (point.start is None) != (point.end is None):
                    raise ValueError('Provide both point start and end, or neither')
                if point.start is not None and not reader.begin_time <= int(point.start) <= tick <= int(point.end) <= reader.end_time:
                    raise ValueError('Point window must contain its time and stay within waveform bounds')
                if point.start is not None and int(point.start) >= int(point.end):
                    raise ValueError('Point window must have positive duration')
                if not set(point.signals) <= descriptors.keys():
                    raise ValueError('Each point signal must be selected in analysis.signals')
                values = {}
                for name in point.signals:
                    events = self.events(reader, descriptors[name], tick, tick)
                    if not events:
                        raise ValueError('No recorded state at point for '+name)
                    values[name] = events[-1]['value']
                points.append({**point.model_dump(), 'values': values})
            points.sort(key=lambda point: int(point['time']))
            bounds = (str(reader.begin_time), str(reader.end_time))
        if self.fingerprint() != before:
            raise ValueError('Waveform changed during analysis; inspect again')
        with _LOCK:
            current = self.read()
            if current['revision'] != expected_revision:
                raise ValueError('Analysis revision changed; read and retry')
            payload = selected.model_dump(exclude={'points'})
            payload.update(points=points, begin=bounds[0], end=bounds[1])
            atomic(self.file, dict(revision=expected_revision+1, waveform=self.waveform, sha256=before, analysis=payload))
        return self.read()

    @staticmethod
    def events(reader, signal, start, end):
        """Decode four-state changes in native ticks, including the state at window start."""
        maps = [dict(zip('01xz', values)) for values in ((0,1,0,0),(0,0,1,0),(0,0,0,1))]
        traces = [reader.load_value_changes(signal, mapping, begin_time=start, end_time=end+1)['mapped'] for mapping in maps]
        rows = []
        if len({len(trace) for trace in traces}) != 1:
            raise ValueError('Waveform reader returned inconsistent four-state traces')
        for value, unknown, highz in zip(*traces):
            if not value[0] == unknown[0] == highz[0]:
                raise ValueError('Waveform reader returned misaligned four-state traces')
            bits = ''.join('x' if unknown[1] >> bit & 1 else 'z' if highz[1] >> bit & 1 else str(value[1] >> bit & 1) for bit in reversed(range(signal.width)))
            rows.append(dict(time=str(value[0]), value=bits))
        return rows
