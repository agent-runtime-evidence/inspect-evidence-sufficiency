"""Tests for the optional Inspect .eval coverage reader.

Only the pure-python mapping (``eval_log_to_coverage_trace``) is exercised here;
it reads attributes/keys via ``getattr`` so it runs without ``inspect-ai``
installed. The actual ``.eval`` file open (``read_eval_log_coverage``) needs the
extra and is not unit-tested here.
"""

from __future__ import annotations

import types
import unittest

from inspect_evidence_sufficiency.coverage import build_coverage_card
from inspect_evidence_sufficiency.inspect_coverage import eval_log_to_coverage_trace


def _fake_tool_call(function: str, call_id: str) -> types.SimpleNamespace:
    # A serialized Inspect ToolCall also carries an extra `internal` passthrough
    # key; the reader must ignore it and keep function/arguments/id.
    return types.SimpleNamespace(
        function=function,
        arguments={"x": 1},
        id=call_id,
        internal={"provider": "opaque-passthrough"},
    )


def _fake_log_with_scored_transfer() -> types.SimpleNamespace:
    assistant = types.SimpleNamespace(
        role="assistant",
        content="doing the transfer",
        tool_calls=[_fake_tool_call("transfer", "c1")],
        metadata={"suspicion_score": 0.2, "reason": "matches approved invoice"},
    )
    tool_result = types.SimpleNamespace(role="tool", tool_call_id="c1", content="ok")
    sample = types.SimpleNamespace(
        messages=[assistant, tool_result],
        scores={"payments_monitor": types.SimpleNamespace(value=0.2)},
        metadata={},
    )
    return types.SimpleNamespace(samples=[sample], eval=types.SimpleNamespace(metadata={}))


class EvalLogMappingTests(unittest.TestCase):
    def test_maps_tool_calls_and_metadata(self) -> None:
        trace = eval_log_to_coverage_trace(_fake_log_with_scored_transfer())
        self.assertEqual(trace["monitor"], "payments_monitor")  # from sample.scores key
        assistant = trace["messages"][0]
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual(assistant["tool_calls"][0]["function"], "transfer")
        self.assertEqual(assistant["tool_calls"][0]["id"], "c1")
        self.assertNotIn("internal", assistant["tool_calls"][0])  # passthrough dropped
        self.assertEqual(assistant["metadata"]["suspicion_score"], 0.2)

    def test_mapped_trace_cards_as_covered(self) -> None:
        trace = eval_log_to_coverage_trace(_fake_log_with_scored_transfer())
        card = build_coverage_card(trace, risky_tools=["transfer"])
        self.assertTrue(card["monitor_identity"]["present"])
        self.assertEqual(card["coverage_ratio"], 1.0)
        self.assertEqual(card["verdict"], "covered")

    def test_monitor_identity_falls_back_to_eval_metadata(self) -> None:
        assistant = types.SimpleNamespace(
            role="assistant",
            content="x",
            tool_calls=[_fake_tool_call("delete_record", "c1")],
            metadata={},
        )
        sample = types.SimpleNamespace(messages=[assistant], scores=None, metadata={})
        log = types.SimpleNamespace(
            samples=[sample], eval=types.SimpleNamespace(metadata={"monitor": "declared_in_eval"})
        )
        trace = eval_log_to_coverage_trace(log)
        self.assertEqual(trace["monitor"], "declared_in_eval")

    def test_empty_log_is_empty_trace(self) -> None:
        trace = eval_log_to_coverage_trace(types.SimpleNamespace(samples=[], eval=None))
        self.assertEqual(trace["messages"], [])
        self.assertIsNone(trace["monitor"])


if __name__ == "__main__":
    unittest.main()
