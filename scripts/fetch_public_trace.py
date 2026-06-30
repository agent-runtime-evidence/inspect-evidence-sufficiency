#!/usr/bin/env python3
"""Fetch one small public demo input, pinned and checksum-verified.

Sources are declared in ``examples/source_manifest.json``: each is pinned to a
dataset commit revision and an expected sha256. The fetched bytes are written
under ``examples/public-traces/`` (git-ignored) and verified against the
recorded hash — a mismatch is a hard failure, never a silent overwrite. Raw
contents are not vendored into the repo.

    python3 scripts/fetch_public_trace.py --source trace-commons
    python3 scripts/fetch_public_trace.py --source assayo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "source_manifest.json"


def _hf_resolve_url(dataset: str, revision: str, file: str) -> str:
    return f"https://huggingface.co/datasets/{dataset}/resolve/{revision}/{file}"


def main(argv: list[str] | None = None) -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sources = manifest.get("sources", {})

    parser = argparse.ArgumentParser(description="Fetch a pinned, checksum-verified public demo input.")
    parser.add_argument(
        "--source",
        default="trace-commons",
        choices=sorted(sources),
        help="source key from examples/source_manifest.json",
    )
    parser.add_argument("--output", default=None, help="override the manifest output path")
    args = parser.parse_args(argv)

    src = sources[args.source]
    url = src.get("url") or _hf_resolve_url(src["dataset"], src["revision"], src["file"])
    out = Path(args.output or src["output"])
    expected = src.get("sha256")
    out.parent.mkdir(parents=True, exist_ok=True)

    # Idempotent: keep an already-correct local copy without re-fetching.
    if out.exists() and expected and hashlib.sha256(out.read_bytes()).hexdigest() == expected:
        print(f"{out} (cached, sha256 verified)")
        return 0

    with urllib.request.urlopen(url, timeout=60) as response:
        raw = response.read()

    actual = hashlib.sha256(raw).hexdigest()
    if expected and actual != expected:
        sys.stderr.write(
            "checksum mismatch — refusing to write a different file than was pinned.\n"
            f"  source:   {args.source}\n  url:      {url}\n"
            f"  expected: {expected}\n  actual:   {actual}\n"
        )
        return 1

    out.write_bytes(raw)
    print(f"{out} ({len(raw)} bytes, sha256 {'verified' if expected else actual})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
