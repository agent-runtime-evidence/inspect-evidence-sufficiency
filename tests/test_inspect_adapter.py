"""Tests for the Inspect adapter.

Two layers:

* offline mapping tests (always run): exercise ``taskstate_to_trace`` against a
  duck-typed fake state, so the mapping logic is covered without ``inspect-ai``;
* an end-to-end scorer test (CI "inspect" lane): build a real ``TaskState``, run
  the real Inspect scorer, and assert the card reflects model/tools/scorer
  evidence and is embedded in ``Score.metadata``.
"""

from __future__ import annotations

import asyncio
import types
import unittest

from inspect_evidence_sufficiency import build_card
from inspect_evidence_sufficiency.inspect_adapter import (
    INSPECT_AVAILABLE,
    taskstate_to_trace,
)


def _read_file():  # a plain callable stands in for a tool offline (name = __name__)
    return None


def _fake_state():
    """Duck-typed stand-in for an Inspect TaskState with rich evidence."""
    return types.SimpleNamespace(
        model="openai/gpt-4o-2024-08-06",
        sample_id="sample-7",
        epoch=1,
        uuid="run-abc123",
        input="solve the task",
        messages=[{"role": "user", "content": "solve the task"}],
        output={"model": "openai/gpt-4o-2024-08-06", "content": "done"},
        tools=[_read_file],
        tool_choice="auto",
        scores={"accuracy": {"value": 1.0}, "evidence_sufficiency": {"value": 0.6}},
        metadata={"release_decision": "support a gate?", "eval_objective": "probe"},
        completed=True,
    )


class TaskStateMappingTests(unittest.TestCase):
    def test_taskstate_to_trace_maps_real_fields(self) -> None:
        trace = taskstate_to_trace(_fake_state(), target="answer")
        self.assertEqual(trace["model"], "openai/gpt-4o-2024-08-06")
        self.assertIn("_read_file", trace["tools"])  # tool name extracted
        self.assertEqual(sorted(trace["scorers"]), ["accuracy", "evidence_sufficiency"])
        self.assertEqual(trace["uuid"], "run-abc123")
        self.assertEqual(trace["source_kind"], "inspect-taskstate")

    def test_mapped_trace_lifts_model_tool_scorer_fields(self) -> None:
        card = build_card(
            taskstate_to_trace(_fake_state(), target="answer"),
            release_decision="support a gate?",
            eval_objective="probe",
        )
        status = {f["id"]: f["status"] for f in card["card_fields"]}
        self.assertEqual(status["F03"], "present")  # dated model => resolved binding
        self.assertIn(status["F05"], {"present", "partial"})  # tools visible
        self.assertEqual(status["F07"], "present")  # scorer identity visible

    def test_empty_state_is_honestly_thin(self) -> None:
        bare = types.SimpleNamespace(
            model="",
            sample_id=None,
            epoch=0,
            uuid=None,
            input=None,
            messages=[],
            output=None,
            tools=[],
            tool_choice=None,
            scores=None,
            metadata={},
            completed=False,
        )
        card = build_card(taskstate_to_trace(bare), release_decision="d", eval_objective="o")
        status = {f["id"]: f["status"] for f in card["card_fields"]}
        self.assertEqual(status["F07"], "missing")  # no scorer => honestly missing
        self.assertIn(card["score_summary"]["overall_verdict"], {"insufficient", "conditional"})


@unittest.skipUnless(INSPECT_AVAILABLE, "inspect-ai not installed; run in the CI 'inspect' lane")
class InspectScorerEndToEndTests(unittest.TestCase):
    def test_scorer_runs_and_embeds_card(self) -> None:
        from inspect_ai.model import ChatMessageAssistant, ChatMessageUser, ModelName, ModelOutput
        from inspect_ai.scorer import Score, Target
        from inspect_ai.solver import TaskState
        from inspect_ai.tool import tool

        from inspect_evidence_sufficiency.inspect_adapter import evidence_sufficiency

        @tool
        def read_file():
            async def execute(path: str):
                """Read a file.

                Args:
                    path: file path
                """
                return "ok"

            return execute

        state = TaskState(
            model=ModelName("openai/gpt-4o-2024-08-06"),
            sample_id="s1",
            epoch=1,
            input="run the task",
            messages=[ChatMessageUser(content="run the task"), ChatMessageAssistant(content="ok")],
            output=ModelOutput.from_content("openai/gpt-4o-2024-08-06", "done"),
            metadata={"release_decision": "support the gate?", "eval_objective": "probe evidence"},
            scores={"accuracy": Score(value=1.0, answer="yes")},
        )
        state.tools = [read_file()]

        scorer_fn = evidence_sufficiency(
            release_decision="support the gate?",
            eval_objective="probe evidence",
        )
        score = asyncio.run(scorer_fn(state, Target("")))

        self.assertIn("evidence_sufficiency_card", score.metadata)
        card = score.metadata["evidence_sufficiency_card"]
        self.assertEqual(card["schema"], "evidence-sufficiency-card.v0")
        self.assertEqual(score.value, card["score_summary"]["sufficiency_score"])
        status = {f["id"]: f["status"] for f in card["card_fields"]}
        self.assertEqual(status["F03"], "present")  # state.model is dated
        self.assertIn(status["F05"], {"present", "partial"})  # tools mapped from state.tools
        self.assertEqual(status["F07"], "present")  # scorer identity mapped from state.scores


if __name__ == "__main__":
    unittest.main()
