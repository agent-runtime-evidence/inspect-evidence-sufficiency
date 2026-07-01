from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from inspect_evidence_sufficiency.coverage import (
    VERDICT_COVERED,
    VERDICT_PARTIAL,
    VERDICT_UNCOVERED,
    build_coverage_card,
    summarize_coverage_card,
)
from inspect_evidence_sufficiency.coverage_cli import (
    EXIT_GATE_BLOCKED,
    EXIT_OK,
    EXIT_USAGE_ERROR,
    _gate_exit_code,
    main,
)

ROOT = Path(__file__).resolve().parents[1]
COV_DIR = ROOT / "examples" / "monitor-coverage"
COVERED = COV_DIR / "trace-covered.json"
UNCOVERED = COV_DIR / "trace-uncovered.json"


def _statuses(card: dict) -> dict:
    return {"verdict": card["verdict"], "coverage": card["coverage_ratio"]}


class CoverageComputationTests(unittest.TestCase):
    """The metric itself: T (tool turns), S (monitored risky turns), coverage."""

    def test_covered_fixture_is_full_coverage(self) -> None:
        card = build_coverage_card(COVERED)
        self.assertEqual(card["schema"], "monitor-coverage-card.v0")
        self.assertTrue(card["monitor_identity"]["present"])
        self.assertEqual(card["coverage_ratio"], 1.0)
        self.assertEqual(card["risky_turn_count"], card["monitored_risky_turn_count"])
        self.assertEqual(card["uncovered_spans"], [])
        self.assertEqual(card["verdict"], VERDICT_COVERED)

    def test_uncovered_fixture_leaves_risky_turns_unmonitored(self) -> None:
        # Default policy: all tool-bearing turns are risky. The transfer and the
        # delete_record turns carry no suspicion_score, so they are uncovered.
        card = build_coverage_card(UNCOVERED)
        self.assertLess(card["coverage_ratio"], 1.0)
        self.assertEqual(card["verdict"], VERDICT_PARTIAL)
        uncovered_tools = {t for span in card["uncovered_spans"] for t in span["tools"]}
        self.assertIn("transfer", uncovered_tools)
        self.assertIn("delete_record", uncovered_tools)

    def test_risky_tools_policy_narrows_the_risky_set(self) -> None:
        # Restricting the risky set to the funds-movement + delete tools, neither of
        # which is monitored, drives coverage to 0.0 -> uncovered.
        card = build_coverage_card(UNCOVERED, risky_tools=["transfer", "delete_record"])
        self.assertEqual(card["risky_set"], ["delete_record", "transfer"])
        self.assertEqual(card["coverage_ratio"], 0.0)
        self.assertEqual(card["verdict"], VERDICT_UNCOVERED)

    def test_monitor_scores_the_turn_not_the_call(self) -> None:
        # Two tool calls in ONE assistant turn share the single suspicion_score on
        # that message: it counts as one monitored risky turn, not two.
        trace = {
            "monitor": "m",
            "messages": [
                {
                    "role": "assistant",
                    "content": "batch",
                    "tool_calls": [
                        {"function": "transfer", "id": "c1"},
                        {"function": "delete_record", "id": "c2"},
                    ],
                    "metadata": {"suspicion_score": 0.3},
                }
            ],
        }
        card = build_coverage_card(trace)
        self.assertEqual(card["tool_turn_count"], 1)
        self.assertEqual(card["risky_turn_count"], 1)
        self.assertEqual(card["monitored_risky_turn_count"], 1)
        self.assertEqual(card["coverage_ratio"], 1.0)
        self.assertEqual(card["verdict"], VERDICT_COVERED)

    def test_no_identified_monitor_is_uncovered_even_with_scores(self) -> None:
        # A suspicion_score with no run-level monitor identity cannot be attributed
        # to an accountable monitor, so the verdict is uncovered.
        trace = [
            {
                "role": "assistant",
                "content": "x",
                "tool_calls": [{"function": "transfer", "id": "c1"}],
                "metadata": {"suspicion_score": 0.2},
            }
        ]
        card = build_coverage_card(trace)
        self.assertFalse(card["monitor_identity"]["present"])
        self.assertEqual(card["verdict"], VERDICT_UNCOVERED)

    def test_monitor_name_override_supplies_identity(self) -> None:
        trace = [
            {
                "role": "assistant",
                "content": "x",
                "tool_calls": [{"function": "transfer", "id": "c1"}],
                "metadata": {"suspicion_score": 0.2},
            }
        ]
        card = build_coverage_card(trace, monitor_name="declared_monitor")
        self.assertTrue(card["monitor_identity"]["present"])
        self.assertEqual(card["monitor_identity"]["name"], "declared_monitor")
        self.assertEqual(card["verdict"], VERDICT_COVERED)

    def test_boolean_suspicion_score_is_not_a_score(self) -> None:
        # bool is an int subclass in Python; a True/False must not count as a score.
        trace = {
            "monitor": "m",
            "messages": [
                {
                    "role": "assistant",
                    "content": "x",
                    "tool_calls": [{"function": "transfer", "id": "c1"}],
                    "metadata": {"suspicion_score": True},
                }
            ],
        }
        card = build_coverage_card(trace)
        self.assertEqual(card["monitored_risky_turn_count"], 0)
        self.assertEqual(card["verdict"], VERDICT_UNCOVERED)

    def test_no_risky_turns_is_covered_with_monitor(self) -> None:
        # A monitored run with no tool calls has nothing risky to leave uncovered.
        trace = {"monitor": "m", "messages": [{"role": "assistant", "content": "just text"}]}
        card = build_coverage_card(trace)
        self.assertEqual(card["risky_turn_count"], 0)
        self.assertEqual(card["coverage_ratio"], 1.0)
        self.assertEqual(card["verdict"], VERDICT_COVERED)

    def test_card_is_deterministic_given_fixed_timestamp(self) -> None:
        import datetime as dt

        now = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
        a = build_coverage_card(COVERED, now=now)
        b = build_coverage_card(COVERED, now=now)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_summary_lists_uncovered_spans(self) -> None:
        card = build_coverage_card(UNCOVERED)
        text = summarize_coverage_card(card)
        self.assertIn("verdict: partial-coverage", text)
        self.assertIn("transfer", text)


class GateMappingTests(unittest.TestCase):
    def test_report_mode_never_blocks(self) -> None:
        for verdict in (VERDICT_COVERED, VERDICT_PARTIAL, VERDICT_UNCOVERED):
            code, _ = _gate_exit_code({"verdict": verdict}, "report")
            self.assertEqual(code, EXIT_OK)

    def test_gate_blocks_unless_covered(self) -> None:
        self.assertEqual(_gate_exit_code({"verdict": VERDICT_COVERED}, "gate")[0], EXIT_OK)
        self.assertEqual(_gate_exit_code({"verdict": VERDICT_PARTIAL}, "gate")[0], EXIT_GATE_BLOCKED)
        self.assertEqual(_gate_exit_code({"verdict": VERDICT_UNCOVERED}, "gate")[0], EXIT_GATE_BLOCKED)


class CoverageCliTests(unittest.TestCase):
    def test_demo_traces_exist(self) -> None:
        self.assertTrue(COVERED.is_file())
        self.assertTrue(UNCOVERED.is_file())

    def test_covered_gate_passes(self) -> None:
        self.assertEqual(main([str(COVERED), "--gate", "--format", "summary"]), EXIT_OK)

    def test_uncovered_gate_blocks(self) -> None:
        self.assertEqual(main([str(UNCOVERED), "--gate", "--format", "summary"]), EXIT_GATE_BLOCKED)

    def test_require_covered_alias_blocks_uncovered(self) -> None:
        self.assertEqual(main([str(UNCOVERED), "--require-covered", "--format", "summary"]), EXIT_GATE_BLOCKED)

    def test_default_reporting_passes_even_when_uncovered(self) -> None:
        self.assertEqual(main([str(UNCOVERED), "--format", "summary"]), EXIT_OK)

    def test_risky_tools_flag_blocks_uncovered_funds_move(self) -> None:
        code = main([str(UNCOVERED), "--risky-tools", "transfer,delete_record", "--gate", "--format", "summary"])
        self.assertEqual(code, EXIT_GATE_BLOCKED)

    def test_pregenerated_card_passthrough(self) -> None:
        card = build_coverage_card(COVERED)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.json"
            path.write_text(json.dumps(card), encoding="utf-8")
            # A covered card passed through verbatim still passes the gate.
            self.assertEqual(main([str(path), "--gate", "--format", "summary"]), EXIT_OK)

    def test_malformed_pregenerated_card_is_usage_error(self) -> None:
        bad = {"schema": "monitor-coverage-card.v0", "version": 1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                main([str(path), "--gate", "--format", "summary"])
            self.assertEqual(ctx.exception.code, EXIT_USAGE_ERROR)

    def test_scalar_top_level_is_usage_error(self) -> None:
        for scalar in ("null", "42", '"a string"'):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "scalar.json"
                path.write_text(scalar, encoding="utf-8")
                with self.assertRaises(SystemExit) as ctx:
                    main([str(path), "--gate", "--format", "summary"])
                self.assertEqual(ctx.exception.code, EXIT_USAGE_ERROR, f"{scalar!r} should be a usage error")

    def test_broken_json_is_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("not json {{{", encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                main([str(path), "--gate", "--format", "summary"])
            self.assertEqual(ctx.exception.code, EXIT_USAGE_ERROR)

    def test_message_not_object_is_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "badmsg.json"
            path.write_text('["not-a-dict"]', encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                main([str(path), "--format", "summary"])
            self.assertEqual(ctx.exception.code, EXIT_USAGE_ERROR)

    def test_dangling_tool_call_id_is_usage_error(self) -> None:
        trace = [
            {"role": "assistant", "tool_calls": [{"function": "transfer", "id": "c1"}]},
            {"role": "tool", "tool_call_id": "does-not-exist"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dangle.json"
            path.write_text(json.dumps(trace), encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                main([str(path), "--format", "summary"])
            self.assertEqual(ctx.exception.code, EXIT_USAGE_ERROR)

    def test_jsonl_trace_is_read(self) -> None:
        lines = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "x",
                "tool_calls": [{"function": "transfer", "id": "c1"}],
                "metadata": {"suspicion_score": 0.2},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
            # No top-level monitor field in a bare list -> uncovered -> gate blocks.
            self.assertEqual(main([str(path), "--gate", "--format", "summary"]), EXIT_GATE_BLOCKED)
            # With a declared monitor, the single scored risky turn is covered.
            self.assertEqual(main([str(path), "--monitor", "m", "--gate", "--format", "summary"]), EXIT_OK)


class StdoutHygieneTests(unittest.TestCase):
    """`--format json` stdout must be a clean, parseable card; the gate line is stderr."""

    def test_json_format_stdout_parses(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main([str(COVERED), "--format", "json"])
        self.assertEqual(code, EXIT_OK)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["schema"], "monitor-coverage-card.v0")

    def test_gate_line_goes_to_stderr(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            main([str(UNCOVERED), "--format", "summary"])
        self.assertIn("gate:", err.getvalue())
        self.assertNotIn("gate:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
