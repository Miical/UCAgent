"""Validate directly compiled Picker packages for the FlowCanvas workflow."""
import hashlib
import json
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
from ucagent.checkers.base import Checker
from .targets import atomic


BUILD_RECEIPT = ".flowcanvas-picker-build.json"


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
    with tempfile.TemporaryDirectory(prefix='flowcanvas-picker-load-') as directory:
        subprocess.run([sys.executable, '-B', '-c', code], cwd=directory,
                       check=True, timeout=60, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def digest(package, suffixes):
    """Fingerprint the installed files needed to load the DUT."""
    return {str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(package.rglob('*')) if p.is_file() and p.suffix in suffixes}


def build_inputs(spec, run_root):
    """Describe the default export inputs independently of the workflow run ID."""
    source = spec['source_file']
    hashes = {**spec.get('picker_source_hashes', {}), source: spec['source_hash']}
    arguments = shlex.split(spec.get('picker_inputs', shlex.quote(source)))
    if arguments[:2] == ['--filelist', str(run_root / 'picker-sources.txt')]:
        sources = (run_root / 'picker-sources.txt').read_text().splitlines()
        if not set(sources) <= hashes.keys():
            raise ValueError('Picker file list contains untracked RTL')
        arguments = sources + arguments[2:]
    elif arguments != [source] or b'`include' in Path(source).read_bytes():
        # An explicitly selected file has no resolved project dependency list.
        return None
    # Bump the version if the standard export options change.
    return {'version': 1, 'dut': spec['dut'], 'arguments': arguments, 'sources': hashes}


def reuse_candidate(package, inputs):
    """Find matching build evidence for the currently installed package."""
    if inputs is None:
        return False
    receipt = package / BUILD_RECEIPT
    try:
        if receipt.exists():
            previous = json.loads(receipt.read_text())
            return (previous.get('inputs') == inputs
                    and previous.get('artifacts') == digest(package, {'.so', '.dylib', '.py'}))
        # Older FlowCanvas runs used the version 1 options and recorded native hashes.
        if inputs['version'] != 1 or not (package / '__init__.py').is_file():
            return False
        libraries = digest(package, {'.so', '.dylib'})
        if not libraries:
            return False
        for run in (package.parent.parent / 'output/flowcanvas/workflow-runs').iterdir():
            if not (run / 'complete.json').is_file():
                continue
            try:
                previous = json.loads((run / 'request.json').read_text())
                if previous.get('dut_output') != str(package) or build_inputs(previous, run) != inputs:
                    continue
                result = json.loads((run / 'workspace/summary/picker-result.json').read_text())
                if (result.get('load_check') == 'passed' and result.get('reused') is False
                        and result.get('libraries') == libraries):
                    return True
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
    except (OSError, ValueError, AttributeError):
        pass
    return False


class PickerPackageChecker(Checker):
    """Accept a fresh or verified reusable Python DUT package."""

    accepted_stage_args = ("reuse_existing", "cache_build")

    def __init__(self, **kwargs):
        """Initialize the package gate."""
        super().__init__()

    def do_check(self, reuse_existing=False, cache_build=True, **kwargs):
        """Verify source identity, build provenance and actual DUT construction."""
        try:
            if not isinstance(reuse_existing, bool) or not isinstance(cache_build, bool):
                raise ValueError("reuse_existing and cache_build must be booleans")
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
            inputs = build_inputs(spec, request.parent)
            artifacts = digest(package, {'.so', '.dylib', '.py'})
            if reuse_existing and not reuse_candidate(package, inputs):
                raise ValueError('No verified Picker package matches the current RTL and export inputs')
            smoke(package)
            if artifacts != digest(package, {'.so', '.dylib', '.py'}):
                raise ValueError('Picker package changed during validation; retry Complete')
            if inputs is not None:
                atomic(package / BUILD_RECEIPT, {'inputs': inputs, 'artifacts': artifacts if cache_build else None})
            evidence = {'output': str(package), 'libraries': {
                name: value for name, value in artifacts.items() if Path(name).suffix in {'.so', '.dylib'}
            }, 'load_check': 'passed', 'reused': reuse_existing}
            atomic(Path(self.workspace) / 'summary/picker-result.json', evidence)
            return True, evidence
        except Exception as exc:
            return False, {'error': str(exc), 'next_action': 'Compile the specified RTL with picker export, inspect its build log, then retry Complete.'}
