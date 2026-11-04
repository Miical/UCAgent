"""Backend contract: models, open, send, interrupt, close and normalized events."""
from .codex import CodexBackend
from .opencode import OpenCodeBackend

BACKENDS = {'codex': CodexBackend, 'opencode': OpenCodeBackend}
NAMES = {'codex': 'Codex', 'opencode': 'OpenCode'}
