"""Reuse UCAgent document/coverage checks and reconcile FlowCanvas target bindings."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from ucagent.checkers.base import Checker
from ucagent.checkers.unity_test import (
    UnityChipCheckerMarkdownFileFormat, UnityChipCheckerLabelStructure,
    UnityChipCheckerCoverageGroup, fc,
)
from .targets import Targets


def integration_snapshot(project, output, source):
    """Fingerprint RTL, test entry points and runner configuration outside coverage output."""
    project, output = Path(project).resolve(), Path(output).resolve()
    paths = {Path(source).resolve(), project / '.flowcanvas/testcases.json'}
    for base, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in {'.git', '.venv', 'node_modules', '__pycache__', '.flowcanvas', 'output'}
                   and not (Path(base) / d).resolve().is_relative_to(output)]
        for name in files:
            p = Path(base) / name
            if p.suffix in {'.v', '.sv', '.vh', '.svh'} or name == 'conftest.py' or (name.startswith('test_') and p.suffix == '.py'):
                paths.add(p)
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in sorted(paths)}


class GoalPlanningChecker(Checker):
    """Gate planning outputs without requiring DUT fixtures or real coverage hits."""

    def __init__(self, project_root, output_dir, phase, **kwargs):
        super().__init__()
        self.project_root = project_root
        self.output_dir = output_dir
        self.phase = phase

    def check_core(self, checker):
        """Run the upstream checker against project-relative deliverables."""
        checker.workspace = self.project_root
        return checker.do_check()

    def do_check(self, **kwargs):
        """Validate documents, code structure and live target bindings at each boundary."""
        try:
            project = Path(self.project_root).resolve()
            output = project / self.output_dir
            if not output.resolve().is_relative_to(project):
                raise ValueError('Coverage output escapes project')
            request = Path(self.workspace).parent / 'request.json'
            if request.is_file():
                spec = json.loads(request.read_text())
                if spec.get('goal_baseline') != integration_snapshot(project, output, spec['source_file']):
                    raise ValueError('RTL, test entry points or runner configuration changed; this workflow must not integrate tests')
            if self.phase == 'understand':
                for name in ('requirements.md', 'design.md'):
                    if not (output / name).is_file() or not (output / name).read_text().strip():
                        raise ValueError('Missing or empty planning document: ' + name)
                    ok, result = self.check_core(UnityChipCheckerMarkdownFileFormat(
                        markdown_file_list=str(Path(self.output_dir) / name)))
                    if not ok:
                        return ok, result
                return True, 'Requirements and design notes checked'
            document = str(Path(self.output_dir) / 'functions_and_checks.md')
            for kind in ('FG', 'FC', 'CK'):
                ok, result = self.check_core(UnityChipCheckerLabelStructure(
                    doc_file=document, leaf_node=kind, must_have_prefix=None))
                if not ok:
                    return ok, result
            if self.phase == 'define':
                return True, 'FG/FC/CK structure checked'
            marks = {mark for kind in ('FG', 'FC', 'CK')
                     for mark in fc.get_unity_chip_doc_marks(str(project / document), kind, 1)}
            mapping = json.loads((output / 'target-map.json').read_text())
            if not isinstance(mapping, dict) or set(mapping) != marks:
                raise ValueError('target-map.json must map every full FG/FC/CK path to one FlowCanvas target ID')
            if len(set(mapping.values())) != len(mapping):
                raise ValueError('Each document target needs a distinct FlowCanvas ID')
            state = Targets(project).status()
            targets = {row['id']: row for row in state['targets']}
            if set(mapping.values()) != set(targets):
                raise ValueError('The complete project target list must be reconciled with target-map.json; reuse existing IDs rather than duplicating or omitting targets')
            for mark, identity in mapping.items():
                row = targets.get(identity)
                kind = {1: 'group', 2: 'point', 3: 'testpoint'}[len(mark.split('/'))]
                parent = mapping.get(mark.rsplit('/', 1)[0]) if '/' in mark else None
                if not row or row['kind'] != kind or row.get('parent') != parent or not row['description'].strip():
                    raise ValueError('Missing/mismatched target hierarchy or description: ' + mark)
                if self.phase in {'implement', 'verify'}:
                    if row['lifecycle'] != 'ready' or state['reports'][identity]['status'] != 'bound':
                        raise ValueError('Target must have valid bindings and finish_create: ' + mark)
                    if not any(b['role'] in {'coverage', 'definition'} and b['file'].startswith(self.output_dir + '/') for b in row['bindings']):
                        raise ValueError('Target needs a binding to its coverage implementation: ' + mark)
            if self.phase == 'refine':
                return True, 'Document and workbench hierarchy reconciled'
            ok, result = self.check_core(UnityChipCheckerCoverageGroup(
                test_dir=self.output_dir,
                cov_file=str(Path(self.output_dir) / 'coverage.py'),
                doc_file=document, check_types=['FG', 'FC', 'CK']))
            if not ok:
                return ok, result
            if self.phase == 'verify':
                if not (output / 'README.md').is_file() or not (output / 'README.md').read_text().strip():
                    raise ValueError('README.md must describe the sampling interface and later integration')
                check = output / 'selftest.py'
                if not check.is_file():
                    raise ValueError('Provide selftest.py with synthetic hit/non-hit checks, without instantiating a real DUT')
                run = subprocess.run([sys.executable, '-B', str(check)], cwd=output,
                                     capture_output=True, text=True, timeout=60)
                if run.returncode:
                    return False, {'error': 'Independent coverage self-test failed', 'output': (run.stdout + run.stderr)[-4000:]}
            return True, {'targets': len(mapping), 'coverage_model': 'validated', 'dut_simulated': False}
        except Exception as exc:
            return False, {'error': str(exc), 'next_action': 'Correct the current phase outputs; read verification_targets for live binding diagnostics and retry Complete.'}
