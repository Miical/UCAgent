"""Validate directly compiled Picker packages for the FlowCanvas workflow."""
import hashlib
import json
import shlex
from pathlib import Path
import subprocess
import sys
from ucagent.checkers.base import Checker


def prepare_inputs(root, rtl, run_root):
    """Create a Picker file list and fingerprint its source and header inputs."""
    sources = [root / file for file in rtl['files']]
    headers = [root / file for file in rtl['headers']]
    include_dirs = [root / folder for folder in rtl['include_dirs']]
    for path in sources + headers + include_dirs:
        if not path.resolve().is_relative_to(root):
            raise ValueError('Picker input must remain inside the project: ' + str(path))

    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources + headers}
    filelist = run_root / 'picker-sources.txt'
    filelist.write_text(''.join(str(path) + '\n' for path in sources))
    args = ['--filelist', str(filelist)]
    args.extend('--sflag=-I' + str(folder) for folder in include_dirs)
    if include_dirs:
        args.append('--vflag=' + ' '.join('+incdir+' + str(folder) for folder in include_dirs))
    return {'picker_inputs': shlex.join(args), 'picker_source_hashes': hashes}


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
            for name, expected in spec.get('picker_source_hashes', {}).items():
                if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
                    raise ValueError('RTL dependency changed; start a new compilation workflow: ' + name)
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
