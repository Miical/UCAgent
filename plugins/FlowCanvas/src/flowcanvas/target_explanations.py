"""Structured target explanations with source-grounded implementation and explicit case relationships."""
import hashlib
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

GUIDE=Path(__file__).parent/'guides/target-explanation-guide.md'


class SourceReference(BaseModel):
    """One real project source range supporting a technical explanation."""
    model_config=ConfigDict(extra='forbid')
    file:str=Field(min_length=1,max_length=4096)
    line:int=Field(ge=1)
    end_line:int|None=Field(default=None,ge=1)
    label:str=Field(default='',max_length=200)


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


def validate_explanation(store,target):
    """Verify real source excerpts and case IDs, then attach server-owned source fingerprints."""
    explanation=target.explanation
    if explanation is None:return
    if target.kind not in {'point','testpoint'}:raise ValueError('Only points and testpoints have explanations')
    references=[ref for step in explanation.implementation for ref in [*step.assignments,*step.evidence]]
    files={};root=store.root.resolve()
    for ref in references:
        path=store.file(ref.file).resolve()
        if path.stat().st_size>2*1024*1024:raise ValueError('Explanation source exceeds 2 MB')
        if path not in files:files[path]=path.read_bytes()
        lines=files[path].decode('utf-8').splitlines();end=ref.end_line or ref.line
        if end<ref.line or end>len(lines):raise ValueError('Explanation source range is invalid')
        if isinstance(ref,ImplementationAssignment) and (not ref.expression.strip() or ''.join(ref.expression.split()) not in ''.join('\n'.join(lines[ref.line-1:end]).split())):
            raise ValueError('Explanation expression does not match the cited source')
        ref.file=str(path.relative_to(root))
    for binding in target.bindings:
        path=store.file(binding.file).resolve()
        if path not in files:
            if path.stat().st_size>2*1024*1024:raise ValueError('Explanation source exceeds 2 MB')
            files[path]=path.read_bytes()
    if len({item.case_id for item in explanation.related_cases})!=len(explanation.related_cases):
        raise ValueError('Duplicate related test case')
    if any(not relation.reason.strip() for relation in explanation.related_cases):raise ValueError('Related cases require a nonempty explanation')
    if explanation.related_cases:
        from .testcases import Cases
        known={case.id for case in Cases(root).read().targets}
        if any(item.case_id not in known for item in explanation.related_cases):
            raise ValueError('Related test case does not exist in this project')
    explanation.source_hashes={str(path.relative_to(root)):hashlib.sha256(raw).hexdigest() for path,raw in files.items()}


def explanation_state(store,explanation,cache):
    """Mark explanations stale when a cited or bound source changes, caching each file per projection."""
    if explanation is None:return 'missing'
    for file,expected in explanation.source_hashes.items():
        if file not in cache:
            try:
                path=store.file(file)
                cache[file]=hashlib.sha256(path.read_bytes()).hexdigest() if path.stat().st_size<=2*1024*1024 else None
            except (OSError,ValueError):cache[file]=None
        if cache[file]!=expected:return 'stale'
    return 'available'
