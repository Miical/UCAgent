"""Rebuild UCAgent's pinned Surfer bundle with its local renderer changes."""

import argparse
import base64
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


SURFER_REVISION = "3ab79e7220f937cb3fadf1117f15d344e5f7e990"
BINDGEN_VERSION = "0.2.113"
SOURCE_SHA256 = "3bb51ce1310ae955a688ffb134e5be69023aa0c408a5a541f70c2f68d62c9a69"
PATCHED_SHA256 = "95ec5a20ff96d9c46bf8ce955e1a63ec4f87070b0068c27e40b88b1ea27b3051"
LOCK_SHA256 = "c5ced9f5f40448b8980608201e8fb14d9b24d82ca0003185cb7b1cc5ff236441"
VIEW_SHA256 = "dbedaaeb5e8d2a6384cf332aae7ee427ec415997abed8ca5a685344a591d115d"
PATCHED_VIEW_SHA256 = "0026a885c0fcb0c248cedb15a373d214de416b5f2832e9a8d3029602128a8a13"
FITTED_VIEW_SHA256 = "23ddaa16a0e6cd0c9dcada3092bd1430709b415785ee654128f0dadd65a586be"
FOCUSED_VIEW_SHA256 = "8e0b49fb76e4c6a8381fd7bdd295a69f145566ea4627d52dedf2fc8e091350ef"


def main() -> None:
    """Validate pinned sources, apply the local patch and publish matched web assets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="Surfer checkout at the pinned revision with initialized submodules")
    parser.add_argument("--wasm-bindgen", default="wasm-bindgen",
                        help="Path to wasm-bindgen CLI version " + BINDGEN_VERSION)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[2] / "server/static/surfer",
                        help="Existing bundle directory containing UCAgent's index.html")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    resources = Path(__file__).resolve().parent
    translation = source / "libsurfer/src/translation/mod.rs"
    if hashlib.sha256((source / "Cargo.lock").read_bytes()).hexdigest() != LOCK_SHA256:
        parser.error("Use Surfer revision " + SURFER_REVISION + " with its unmodified Cargo.lock")
    source_hash = hashlib.sha256(translation.read_bytes()).hexdigest()
    if source_hash not in {SOURCE_SHA256, PATCHED_SHA256}:
        parser.error("Translation sources differ from the pinned revision/local patch")
    view = source / "libsurfer/src/view.rs"
    view_hash = hashlib.sha256(view.read_bytes()).hexdigest()
    if (source_hash, view_hash) not in {
        (SOURCE_SHA256, VIEW_SHA256), (PATCHED_SHA256, PATCHED_VIEW_SHA256),
        (PATCHED_SHA256, FITTED_VIEW_SHA256), (PATCHED_SHA256, FOCUSED_VIEW_SHA256),
    }:
        parser.error("Use a clean pinned checkout or one with the complete local patch")
    version = subprocess.check_output([args.wasm_bindgen, "--version"], text=True).strip()
    if version != "wasm-bindgen " + BINDGEN_VERSION:
        parser.error("Install wasm-bindgen-cli version " + BINDGEN_VERSION)
    index = output / "index.html"
    html = index.read_text(encoding="utf-8")
    asset_tags = {
        name: re.compile(r'href="\./' + re.escape(name) + r'(?:\?v=[a-f0-9]+)?("[^>]*integrity=")sha384-[^"]+"')
        for name in ("surfer.js", "surfer_bg.wasm")
    }
    for name, tag in asset_tags.items():
        if len(tag.findall(html)) != 1:
            parser.error("index.html must contain exactly one integrity preload for " + name)
    if source_hash == SOURCE_SHA256:
        subprocess.run(["patch", "--batch", "-p1", "-i", str(resources / "bit_expansion.patch")],
                       cwd=source, check=True)
    if view_hash not in {FITTED_VIEW_SHA256, FOCUSED_VIEW_SHA256}:
        subprocess.run(["patch", "--batch", "-p1", "-i", str(resources / "variable_name_width.patch")],
                       cwd=source, check=True)
    if view_hash != FOCUSED_VIEW_SHA256:
        subprocess.run(["patch", "--batch", "-p1", "-i", str(resources / "signal_focus_color.patch")],
                       cwd=source, check=True)
    shutil.copyfile(resources / "bit_expansion.rs", translation.with_name("bit_expansion.rs"))
    env = dict(os.environ, VERGEN_GIT_SHA=SURFER_REVISION,
               VERGEN_GIT_DESCRIBE="v0.6.0-14-g3ab79e7-ucagent-bits")
    subprocess.run(["cargo", "build", "--release", "--locked", "--target",
                    "wasm32-unknown-unknown", "-p", "surfer", "--no-default-features",
                    "--features", "performance_plot,accesskit"], cwd=source, env=env, check=True)
    target = Path(env.get("CARGO_TARGET_DIR", "target"))
    if not target.is_absolute():
        target = source / target
    with tempfile.TemporaryDirectory(prefix="ucagent-surfer-web-") as directory:
        staging = Path(directory)
        subprocess.run([args.wasm_bindgen, "--target", "web", "--out-name", "surfer",
                        "--out-dir", str(staging),
                        str(target / "wasm32-unknown-unknown/release/surfer.wasm")], check=True)
        for name, tag in asset_tags.items():
            digest = hashlib.sha384((staging / name).read_bytes())
            integrity = base64.b64encode(digest.digest()).decode("ascii")
            version = digest.hexdigest()[:16]
            html = tag.sub(lambda match: f'href="./{name}?v={version}{match[1]}sha384-{integrity}"', html)
        # Publish only after both matching assets and their integrity hashes are ready.
        for name in ("surfer.js", "surfer_bg.wasm"):
            shutil.copyfile(staging / name, output / name)
        index.write_text(html, encoding="utf-8")
    print("Rebuilt Surfer " + SURFER_REVISION + " with the UCAgent renderer changes")


if __name__ == "__main__":
    main()
