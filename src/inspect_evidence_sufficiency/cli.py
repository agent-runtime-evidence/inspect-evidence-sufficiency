"""Command line interface for Evidence-Sufficiency-Card generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .card import build_card, summarize_card


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an Evidence-Sufficiency-Card from an agent eval trace.")
    parser.add_argument("input", help="JSON, JSONL, generated card JSON, or ControlArena export directory")
    parser.add_argument("--source-url", default=None)
    parser.add_argument("--source-title", default=None)
    parser.add_argument("--release-decision", default=None)
    parser.add_argument("--eval-objective", default=None)
    parser.add_argument("--output", "-o", default=None, help="Write JSON card to this path")
    parser.add_argument("--format", choices=["json", "summary"], default="json")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if input_path.is_file() and input_path.suffix == ".json":
        maybe_card = _read_json(input_path)
        if isinstance(maybe_card, dict) and maybe_card.get("schema") == "evidence-sufficiency-card.v0":
            card = maybe_card
        else:
            card = build_card(
                input_path,
                release_decision=args.release_decision,
                eval_objective=args.eval_objective,
                source_url=args.source_url,
                source_title=args.source_title,
            )
    else:
        card = build_card(
            input_path,
            release_decision=args.release_decision,
            eval_objective=args.eval_objective,
            source_url=args.source_url,
            source_title=args.source_title,
        )

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.format == "summary":
        print(summarize_card(card))
    else:
        print(json.dumps(card, indent=2, sort_keys=True))
    return 0


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
