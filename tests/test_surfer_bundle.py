"""Verify the bundled Surfer assets remain paired with their preload integrity hashes."""

import base64
import hashlib
from pathlib import Path
import re

import pytest


@pytest.mark.parametrize("name", ["surfer.js", "surfer_bg.wasm"])
def test_bundle_preload_integrity_matches_asset(name):
    """Reject stale integrity hashes that prevent the actual browser bundle from loading."""
    bundle = Path(__file__).resolve().parents[1] / "ucagent/server/static/surfer"
    matches = re.findall(r'href="\./' + re.escape(name) + r'\?v=([a-f0-9]+)"[^>]*integrity="sha384-([^"]+)"',
                         (bundle / "index.html").read_text(encoding="utf-8"))
    digest = hashlib.sha384((bundle / name).read_bytes())
    assert matches == [(digest.hexdigest()[:16], base64.b64encode(digest.digest()).decode("ascii"))]
