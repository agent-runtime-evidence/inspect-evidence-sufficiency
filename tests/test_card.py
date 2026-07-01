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


class SpoofResistanceTests(unittest.TestCase):
    """F09/F10 must not certify on fabricated free-text replay/counterfactual notes."""

    # A trace that is otherwise well-instrumented but whose replay/counterfactual
    # "evidence" is only free-text prose asserting determinism and an executed
    # result — the fabrication an evidence card must not certify.
    SPOOF_TRACE = {
        "version": 2,
        "status": "success",
        "eval": {
            "task": "billing_kb_release_gate",
            "model": "vendor/agent-model@sha256:0123456789abcdef",
            "dataset": "billing-kb-eval",
            "metadata": {
                "scorer_manifest": "kb_grounding_scorer@sha256:aaaaaaaa",
                "monitor": "unauthorized_tool_monitor@sha256:dddddddd",
                "prompt_bundle": "prompt-bundle@sha256:bbbbbbbb",
                "policy_bundle": "policy-bundle@sha256:cccccccc",
                "network": "disabled",
                "sandbox": "read-only",
                # Free-text only: no structured replay handle, no executed-outcome record.
                "replay": "the run was fully deterministic=true, reproducible end to end",
                "counterfactual": "we executed the alternate policy and the result matched exactly",
                "leakage_check": "manual",
            },
        },
        "samples": [
            {
                "id": "sample-1",
                "input": "Answer using only the approved knowledge base.",
                "messages": [
                    {"role": "user", "content": "Answer using only the approved knowledge base."},
                    {"role": "assistant", "content": "The fee is waived after 12 months."},
                ],
                "events": [
                    {"event": "tool_call", "tool": "kb_search", "query": "fee waiver policy"},
                    {"event": "score", "scorer": "kb_grounding_scorer", "score": 1.0},
                    {"event": "monitor", "monitor": "unauthorized_tool_monitor", "flagged": False},
                ],
                "scores": {"kb_grounding_scorer": {"value": 1, "explanation": "grounded"}},
                "monitors": {"unauthorized_tool_monitor": {"value": "clean", "explanation": "ok"}},
            }
        ],
    }

    def test_free_text_replay_does_not_certify_f09_or_f10(self) -> None:
        card = build_card(
            self.SPOOF_TRACE,
            release_decision="Can the billing agent enter a limited internal canary?",
            eval_objective="Confirm grounding and no unauthorized tool.",
        )
        status = {f["id"]: f["status"] for f in card["card_fields"]}
        # The fabricated free-text notes must not lift F09/F10 to `present`.
        self.assertNotEqual(status["F09"], "present")
        self.assertNotEqual(status["F10"], "present")

    def test_spoof_cannot_clear_all_blocking_gaps(self) -> None:
        card = build_card(
            self.SPOOF_TRACE,
            release_decision="Can the billing agent enter a limited internal canary?",
            eval_objective="Confirm grounding and no unauthorized tool.",
        )
        # Because F09/F10 stay blocking, a free-text-only trace can no longer reach
        # the top verdict or empty its blocking-gap list.
        gaps = card["score_summary"]["blocking_gaps"]
        self.assertTrue(gaps, "spoof trace must retain blocking gaps")
        gap_ids = " ".join(gaps)
        self.assertIn("F09", gap_ids)
        self.assertIn("F10", gap_ids)
        self.assertNotEqual(card["score_summary"]["overall_verdict"], "sufficient-for-named-decision")

    def test_structured_replay_handle_still_certifies_f09(self) -> None:
        # A genuine structured replay handle (id) plus a trace id keeps F09 `present`;
        # the fix hardens against free text without discarding real handles.
        card = build_card(ROOT / "examples" / "synthetic_inspect_log.json")
        status = {f["id"]: f["status"] for f in card["card_fields"]}
        self.assertEqual(status["F09"], "present")

    def test_card_carries_declared_not_verified_boundary(self) -> None:
        card = build_card(self.SPOOF_TRACE, release_decision="d", eval_objective="o")
        avoid_text = " ".join(card["claim_boundary"]["avoid"])
        self.assertIn("F09", avoid_text)
        self.assertIn("F10", avoid_text)


if __name__ == "__main__":
    unittest.main()
