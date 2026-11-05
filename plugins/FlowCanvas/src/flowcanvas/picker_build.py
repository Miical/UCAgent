"""Validate directly compiled Picker packages for the FlowCanvas workflow."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from ucagent.checkers.base import Checker


def smoke(package):
    """Load the generated package and construct its real native DUT."""
    code = ('import sys; sys.path.insert(0,' + repr(str(package.parent)) + '); import importlib; m=importlib.import_module(' + repr(package.name) +
            '); d=getattr(m,' + repr('DUT' + package.name) + ')(); d.Finish()')
    subprocess.run([sys.executable, '-c', code], cwd=package,
                   check=True, timeout=60, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def digest(package):
    """Fingerprint installed native libraries, preventing stale acceptance."""
    return {str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(package.rglob('*')) if p.is_file() and p.suffix in {'.so', '.dylib'}}


class PickerPackageChecker(Checker):
    """Accept a newly compiled and actually loadable Python DUT package."""

    accepted_stage_args = ("reuse_existing",)

    def __init__(self, **kwargs):
        super().__init__()

    def do_check(self, reuse_existing=False, **kwargs):
        """Check fixed output, unchanged source, fresh native DUT and real construction."""
        try:
            if not isinstance(reuse_existing, bool):
                raise ValueError("reuse_existing must be a boolean")
            request = Path(self.workspace).parent / 'request.json'
            spec = json.loads(request.read_text())
            package = Path(spec['dut_output'])
            if not package.resolve().is_relative_to(Path(spec['project_root']).resolve()):
                raise ValueError('DUT output must remain inside the project')
            if hashlib.sha256(Path(spec['source_file']).read_bytes()).hexdigest() != spec['source_hash']:
                raise ValueError('Input RTL changed; start a new compilation workflow')
            native = [p for p in package.rglob('*') if p.is_file()
                      and p.suffix in {'.so', '.dylib'} and spec['dut'] in p.name]
            if not native or (not reuse_existing and max(p.stat().st_mtime for p in native) < spec['build_started_at']):
                raise ValueError('No native DUT compiled during this workflow; old packages cannot pass')
            smoke(package)
            evidence = {'output': str(package), 'libraries': digest(package), 'load_check': 'passed', 'reused': reuse_existing}
            receipt = Path(self.workspace) / 'summary/picker-result.json'
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps(evidence, indent=2))
            return True, evidence
        except Exception as exc:
            return False, {'error': str(exc), 'next_action': 'Compile the specified RTL with picker export, inspect its build log, then retry Complete.'}
