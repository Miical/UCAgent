"""Structured target explanations with source-grounded implementation and explicit case relationships."""
import hashlib
from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .line_bindings import MARKER, fingerprint

GUIDE=Path(__file__).parent/'guides/target-explanation-guide.md'


class SourceAnchor(BaseModel):
    """Server-owned location in non-marker lines following a marker or the file start."""
    model_config=ConfigDict(extra='forbid')
    marker_id:str|None=Field(default=None,pattern=r'^bind_[0-9a-f]{32}$')
    offset:int=Field(ge=0)
    line_count:int=Field(ge=1)
    fingerprint:str=Field(pattern=r'^[0-9a-f]{64}$')


class SourceReference(BaseModel):
    """One real project source range supporting a technical explanation."""
    model_config=ConfigDict(extra='forbid')
    file:str=Field(min_length=1,max_length=4096)
    line:int=Field(ge=1)
    end_line:int|None=Field(default=None,ge=1)
    label:str=Field(default='',max_length=200)
    anchor:SourceAnchor|None=None


class ImplementationAssignment(SourceReference):
    """An exact source expression paired with its system-level meaning."""
    expression:str=Field(min_length=1,max_length=6000)
    meaning:str=Field(min_length=1,max_length=2000)


class ImplementationStep(BaseModel):
    """Explain a stimulus, sample or check while keeping inference distinct from source facts."""
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=160)
    text:str=Field(min_length=1,max_length=3000)
    confidence:Literal['source','inferred']='inferred'
    assignments:list[ImplementationAssignment]=Field(default_factory=list,max_length=12)
    evidence:list[SourceReference]=Field(default_factory=list,max_length=12)

    @model_validator(mode='after')
    def grounded(self):
        """Source claims require an actual cited implementation, not merely an authored marker."""
        if self.confidence=='source' and not (self.assignments or self.evidence):
            raise ValueError('Source implementation explanations require citations')
        return self


class RelatedCase(BaseModel):
    """An explicit, justified relationship to an existing test case."""
    model_config=ConfigDict(extra='forbid')
    case_id:str=Field(pattern='^tc_[0-9a-f]{32}$')
    reason:str=Field(min_length=1,max_length=1000)


class TargetExplanation(BaseModel):
    """Shared fields rendered differently for feature-level and specific test-point understanding."""
    model_config=ConfigDict(extra='forbid')
    summary:str=Field(default='',max_length=3000)
    approach:str=Field(default='',max_length=3000)
    conditions:str=Field(default='',max_length=3000)
    expected:str=Field(default='',max_length=3000)
    check_kind:Literal['unknown','coverage_only','assertion','comparison','mixed']='unknown'
    implementation:list[ImplementationStep]=Field(default_factory=list,max_length=20)
    related_cases:list[RelatedCase]=Field(default_factory=list,max_length=32)
    coverage_notes:str=Field(default='',max_length=3000)
    source_hashes:dict[str,str]=Field(default_factory=dict)

    @model_validator(mode='after')
    def check_evidence(self):
        """A named checking mechanism requires at least one actual implementation citation."""
        if self.check_kind!='unknown' and not any(step.assignments or step.evidence for step in self.implementation):
            raise ValueError('An identified check kind requires implementation evidence')
        return self



BINDING_SOURCE_PREFIX = 'without-binding-markers:'


class SourceLocations:
    """Translate between real file lines and ranges that ignore FlowCanvas markers."""

    def __init__(self, raw):
        source = raw.decode('utf-8')
        self.lines = source.splitlines(keepends=True)
        markers = {}
        counts = Counter()
        self.code_lines = []
        for line, text in enumerate(self.lines, 1):
            match = MARKER.fullmatch(text.rstrip('\r\n'))
            if match:
                marker = match[1]
                markers[marker] = line
                counts[marker] += 1
            else:
                self.code_lines.append(line)
        self.markers = {marker: line for marker, line in markers.items() if counts[marker] == 1}
        content = ''.join(self.lines[line-1] for line in self.code_lines)
        self.filtered_hash = BINDING_SOURCE_PREFIX + hashlib.sha256(content.encode('utf-8')).hexdigest()
        self.raw_hash = hashlib.sha256(raw).hexdigest()

    def anchor(self, start, end):
        first = bisect_left(self.code_lines, start)
        last = bisect_right(self.code_lines, end)
        if end < start or end > len(self.lines) or first == last:
            raise ValueError('Explanation source range is invalid')
        marker_line, marker = max(
            ((line, marker) for marker, line in self.markers.items() if line < start),
            default=(0, None),
        )
        text = ''.join(self.lines[line-1] for line in self.code_lines[first:last])
        return SourceAnchor(marker_id=marker, offset=first-bisect_right(self.code_lines, marker_line),
                            line_count=last-first, fingerprint=fingerprint(text))

    def resolve(self, anchor):
        if anchor.marker_id and anchor.marker_id not in self.markers:
            raise ValueError('Explanation source marker is missing or duplicated')
        marker_line = self.markers.get(anchor.marker_id, 0)
        start = bisect_right(self.code_lines, marker_line) + anchor.offset
        end = start + anchor.line_count
        if end > len(self.code_lines):
            raise ValueError('Explanation source range is invalid')
        text = ''.join(self.lines[line-1] for line in self.code_lines[start:end])
        if fingerprint(text) != anchor.fingerprint:
            raise ValueError('Explanation anchored source has changed')
        return self.code_lines[start], self.code_lines[end-1], text


def read_locations(path):
    if path.stat().st_size>2*1024*1024:
        raise ValueError('Explanation source exceeds 2 MB')
    return SourceLocations(path.read_bytes())


def validate_explanation(store,target):
    """Verify real source excerpts and case IDs, then attach server-owned source fingerprints."""
    explanation=target.explanation
    if explanation is None:return
    if target.kind not in {'point','testpoint'}:raise ValueError('Only points and testpoints have explanations')
    references=[ref for step in explanation.implementation for ref in [*step.assignments,*step.evidence]]
    files={};root=store.root.resolve()
    for ref in references:
        path=store.file(ref.file).resolve()
        if path not in files:
            files[path] = read_locations(path)
        locations = files[path]
        ref.anchor = locations.anchor(ref.line, ref.end_line or ref.line)
        _, _, content = locations.resolve(ref.anchor)
        if isinstance(ref,ImplementationAssignment) and (not ref.expression.strip() or
                ''.join(ref.expression.split()) not in ''.join(content.split())):
            raise ValueError('Explanation expression does not match the cited source')
        ref.file=str(path.relative_to(root))
    for binding in target.bindings:
        path=store.file(binding.file).resolve()
        if path not in files:
            files[path] = read_locations(path)
    if len({item.case_id for item in explanation.related_cases})!=len(explanation.related_cases):
        raise ValueError('Duplicate related test case')
    if any(not relation.reason.strip() for relation in explanation.related_cases):raise ValueError('Related cases require a nonempty explanation')
    if explanation.related_cases:
        from .testcases import Cases
        known={case.id for case in Cases(root).read().targets}
        if any(item.case_id not in known for item in explanation.related_cases):
            raise ValueError('Related test case does not exist in this project')
    explanation.source_hashes = {str(path.relative_to(root)): locations.filtered_hash
                                 for path, locations in files.items()}


def project_explanation(store, explanation, cache):
    """Resolve saved anchors into current line numbers without modifying the persisted offsets."""
    if explanation is None:
        return 'missing', None

    def locations(file):
        if file not in cache:
            try:
                cache[file] = read_locations(store.file(file))
            except (OSError, ValueError):
                cache[file] = None
        return cache[file]

    for file, expected in explanation.source_hashes.items():
        source = locations(file)
        if source is None or expected not in (source.filtered_hash, source.raw_hash):
            return 'stale', None

    projected = explanation.model_copy(deep=True)
    try:
        for step in projected.implementation:
            for ref in [*step.assignments, *step.evidence]:
                if ref.anchor is None:
                    continue
                source = locations(ref.file)
                if source is None:
                    return 'stale', None
                start, end, _ = source.resolve(ref.anchor)
                ref.line = start
                ref.end_line = end if start != end or ref.end_line is not None else None
    except ValueError:
        return 'stale', None
    return 'available', projected
