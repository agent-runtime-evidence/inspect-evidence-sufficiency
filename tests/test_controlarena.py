"""ControlArena export reader test (P2-10): a synthetic export cards end-to-end."""

from __future__ import annotations

import unittest
from pathlib import Path

from inspect_evidence_sufficiency import build_card
from inspect_evidence_sufficiency.controlarena_adapter import load_controlarena_export

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "examples" / "controlarena-export"


class ControlArenaExportTests(unittest.TestCase):
    def test_loader_reads_three_files(self) -> None:
        data = load_controlarena_export(EXPORT)
        self.assertEqual(data["source_kind"], "controlarena-trajectory-export")
        self.assertTrue(data["trajectory"])  # trajectory.jsonl parsed to rows
        self.assertIn("tools", data)
        self.assertIn("metadata", data)

    def test_missing_trajectory_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_controlarena_export(ROOT / "examples")  # no trajectory.jsonl here

    def test_export_cards_with_evidence(self) -> None:
        card = build_card(
            EXPORT,
            release_decision="Can this control run support a release-evidence claim?",
            eval_objective="Probe evidence present/partial/missing in a ControlArena export.",
        )
        self.assertEqual(card["schema"], "evidence-sufficiency-card.v0")
        self.assertEqual(card["source"]["kind"], "controlarena-export")
        status = {f["id"]: f["status"] for f in card["card_fields"]}
        self.assertEqual(status["F02"], "present")  # task + dataset in metadata
        self.assertEqual(status["F07"], "present")  # scorer + monitor identity
        self.assertIn(status["F05"], {"present", "partial"})  # tools.json read
        self.assertIn(card["score_summary"]["overall_verdict"], {"conditional", "insufficient"})


if __name__ == "__main__":
    unittest.main()
