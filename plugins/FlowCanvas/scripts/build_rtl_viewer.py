"""Build the checked-in upstream Vue sources and stage the package's static assets."""
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
viewer = root / 'frontend/rtl-viewer'
subprocess.run(['npm', 'ci', '--no-audit', '--no-fund'], cwd=viewer, check=True)
subprocess.run(['npm', 'run', 'build'], cwd=viewer, check=True)
target = root / 'src/flowcanvas/web/rtl-viewer'
if target.exists():
    shutil.rmtree(target)
shutil.copytree(viewer / 'dist', target, ignore=shutil.ignore_patterns('*.map'))
for filename in ('LICENSE', 'THIRD-PARTY-NOTICES.md'):
    shutil.copy2(viewer / filename, target / filename)
print('Staged RTL viewer:', target)
