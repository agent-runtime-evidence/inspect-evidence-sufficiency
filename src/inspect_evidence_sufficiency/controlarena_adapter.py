"""Reader for ControlArena trajectory exports.

ControlArena documents `control-arena trajectory export LOG OUT_DIR`, producing:
`trajectory.jsonl`, `tools.json`, and `metadata.json`. This module reads that
export shape without depending on ControlArena at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_controlarena_export(directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    trajectory = root / "trajectory.jsonl"
    tools = root / "tools.json"
    metadata = root / "metadata.json"
    if not trajectory.exists():
        raise FileNotFoundError(f"ControlArena export missing {trajectory}")

    data: dict[str, Any] = {
        "source_kind": "controlarena-trajectory-export",
        "trajectory": _load_jsonl(trajectory),
    }
    if tools.exists():
        data["tools"] = json.loads(tools.read_text(encoding="utf-8"))
    if metadata.exists():
        data["metadata"] = json.loads(metadata.read_text(encoding="utf-8"))
    return data


def _load_jsonl(path: Path) -> list[Any]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
