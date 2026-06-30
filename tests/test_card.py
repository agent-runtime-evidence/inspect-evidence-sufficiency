from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from inspect_evidence_sufficiency import build_card

ROOT = Path(__file__).resolve().parents[1]


class EvidenceCardTests(unittest.TestCase):
    def test_synthetic_fixture_has_card_fields(self) -> None:
        card = build_card(ROOT / "examples" / "synthetic_inspect_log.json")
        self.assertEqual(card["schema"], "evidence-sufficiency-card.v0")
        self.assertEqual(len(card["card_fields"]), 12)
        statuses = {field["id"]: field["status"] for field in card["card_fields"]}
        self.assertEqual(statuses["F01"], "present")
        self.assertEqual(statuses["F03"], "present")
        self.assertEqual(statuses["F06"], "present")
        self.assertEqual(statuses["F10"], "partial")

    def test_missing_context_is_insufficient_or_conditional(self) -> None:
        card = build_card([{"type": "message", "message": {"role": "assistant", "content": "ok"}}])
        self.assertIn(card["score_summary"]["overall_verdict"], {"insufficient", "conditional"})
        gap_text = "\n".join(card["score_summary"]["blocking_gaps"])
        self.assertIn("F01", gap_text)
        self.assertIn("F07", gap_text)

    def test_real_inspect_log_fixture_cards_with_evidence(self) -> None:
        # A real Inspect .eval log (JSON format, mockllm) — not hand-built synthetic.
        card = build_card(
            ROOT / "examples" / "inspect-log-mockllm.json",
            release_decision="Can this eval support an engineering review gate?",
            eval_objective="Probe evidence present/partial/missing in a real Inspect log.",
            source_url="inspect-log://mini",
        )
        self.assertEqual(card["schema"], "evidence-sufficiency-card.v0")
        status = {f["id"]: f["status"] for f in card["card_fields"]}
        # A real instrumented Inspect log carries dataset scope, scorer identity, and transcript...
        self.assertEqual(status["F02"], "present")  # task/dataset scope
        self.assertEqual(status["F07"], "present")  # scorer identity (e.g. includes)
        self.assertEqual(status["F06"], "present")  # messages/events
        # ...but is still honestly not sufficient: no counterfactual replay in a plain eval.
        self.assertEqual(status["F10"], "missing")
        self.assertIn(card["score_summary"]["overall_verdict"], {"conditional", "insufficient"})

    def test_card_is_byte_deterministic_with_fixed_now(self) -> None:
        fixed = datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)
        a = build_card(ROOT / "examples" / "synthetic_inspect_log.json", now=fixed)
        b = build_card(ROOT / "examples" / "synthetic_inspect_log.json", now=fixed)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(a["created_utc"], "2026-06-30T12:00:00+00:00")

    def test_jsonl_trace_hash_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            path.write_text('{"type":"message","model":"model-alias","tool":"read_file"}\n', encoding="utf-8")
            card1 = build_card(path, release_decision="d", eval_objective="o")
            card2 = build_card(path, release_decision="d", eval_objective="o")
            self.assertEqual(card1["source"]["sha256"], card2["source"]["sha256"])
            self.assertEqual(
                card1["source"]["sha256"], "4bebb360f27cd26ed59ef55cc30610a4a4faf9a09b043099d78c2ee4f373a4ad"
            )


if __name__ == "__main__":
    unittest.main()
