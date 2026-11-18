"""Stage RTL for project-scoped static analysis and register accepted findings."""
import json
from pathlib import Path
import shutil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from ucagent.checkers.base import Checker
from .bugs import Bugs
from .projects import parse_rtl
from .target_explanations import SourceLocations, SourceReference, read_locations
from .targets import atomic


def prepare_sources(project, source, dut, run):
    """Copy the selected module's dependency closure with original relative paths."""
    if not source.is_relative_to(project):
        raise ValueError('Static analysis RTL must be inside the bound project')
    rtl = parse_rtl(project, dut, source.relative_to(project).as_posix())
    if not rtl['ready']:
        raise ValueError('Cannot resolve static analysis RTL dependencies: ' + '; '.join(rtl['issues']))
    work = run / 'workspace'
    sources = []
    for relative in dict.fromkeys(rtl['files'] + rtl['headers']):
        original = (project / relative).resolve()
        if not original.is_relative_to(project):
            raise ValueError('Static analysis dependency escapes the project: ' + relative)
        snapshot = Path(dut + '_RTL') / relative
        (work / snapshot).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, work / snapshot)
        sources.append(snapshot.as_posix())
    return sources


class StaticFinding(BaseModel):
    """A specific, source-backed suspected issue, without a verification claim."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    condition: str = Field(min_length=1, max_length=2000)
    impact: str = Field(min_length=1, max_length=2000)
    category: Literal['width', 'reset', 'clock', 'state', 'protocol', 'logic', 'other']
    source_refs: list[SourceReference] = Field(min_length=1, max_length=12)


FINDINGS = TypeAdapter(list[StaticFinding])


class StaticBugPanelChecker(Checker):
    """Accept each reviewed RTL file and publish the resulting Bug records at completion."""
    accepted_stage_args = ('reviewed_file', 'findings')

    def __init__(self, **kwargs):
        """Initialize the report-free panel gate."""
        super().__init__()

    def get_template_data(self):
        """Show the next file without requiring a model-authored progress document."""
        root = Path(self.workspace).parent
        request = json.loads((root / 'request.json').read_text())
        progress = root / 'static-findings.json'
        reviewed = json.loads(progress.read_text()) if progress.exists() else {}
        sources = request['static_sources']
        pending = next((file for file in sources if file not in reviewed), '')
        return {'ANALYSIS_PROGRESS': f'{len(reviewed)}/{len(sources)}',
                'CURRENT_STATIC_RTL': pending or 'All files reviewed'}

    def do_check(self, reviewed_file=None, findings=None, is_complete=False, **kwargs):
        """Validate source ranges and submit findings only when all files have been reviewed."""
        try:
            work = Path(self.workspace)
            request = json.loads((work.parent / 'request.json').read_text())
            prefix = request['dut'] + '_RTL/'
            sources = {file: file.removeprefix(prefix) for file in request['static_sources']}
            if not sources:
                raise ValueError('No RTL files to review')
            progress = work.parent / 'static-findings.json'
            reviewed = json.loads(progress.read_text()) if progress.exists() else {}
            pending = next((file for file in sources if file not in reviewed), None)
            if reviewed_file is not None or findings is not None:
                if reviewed_file not in sources or not isinstance(findings, list) or len(findings)>24:
                    raise ValueError('Submit a staged reviewed_file and a findings list with up to 24 entries')
                rows = FINDINGS.validate_python(findings)
                payload = [row.model_dump() for row in rows]
                if reviewed_file in reviewed:
                    if reviewed[reviewed_file] != payload:
                        raise ValueError('This file already has a different accepted submission')
                else:
                    if reviewed_file != pending:
                        raise ValueError('Review the current staged RTL file first: ' + str(pending))
                    staged = {file: snapshot for snapshot, file in sources.items()}
                    locations = {}
                    for row in rows:
                        if row.source_refs[0].file != sources[reviewed_file]:
                            raise ValueError('The primary citation must be in the file being reviewed')
                        for ref in row.source_refs:
                            if ref.file not in staged:
                                raise ValueError('Cite a project-relative file in the staged RTL scope')
                            if ref.file not in locations:
                                locations[ref.file] = read_locations(work / staged[ref.file])
                            locations[ref.file].anchor(ref.line, ref.end_line or ref.line)
                    reviewed[reviewed_file] = payload
                    atomic(progress, reviewed)
                    pending = next((file for file in sources if file not in reviewed), None)
                    if pending:
                        self.reset_continue_fail_count_with_batch_pass()
            result = {'reviewed': len(reviewed), 'total': len(sources), 'next_file': pending}
            if pending:
                return False, result
            if is_complete:
                store = Bugs(request['project_root'])
                rows = [row for batch in reviewed.values() for row in FINDINGS.validate_python(batch)]
                cited = {ref.file for row in rows for ref in row.source_refs}
                locations = {}
                for snapshot, relative in sources.items():
                    current = store.file(relative).read_bytes()
                    if current != (work / snapshot).read_bytes():
                        raise ValueError('RTL changed during analysis; start a new run: ' + relative)
                    if relative in cited:
                        locations[relative] = SourceLocations(current)
                result.update(store.register_static_findings(rows, locations))
            return True, result
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return False, {'error': str(exc)}
