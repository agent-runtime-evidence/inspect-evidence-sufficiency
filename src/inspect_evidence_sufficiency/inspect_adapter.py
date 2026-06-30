"""Optional Inspect AI scorer wrapper.

The scorer maps an Inspect ``TaskState`` into the trace dict the card extractor
reads. It deliberately reads the fields Inspect actually records on the state
(model, tools, tool_choice, scores, sample_id/epoch/uuid, messages, output,
metadata) instead of a narrow metadata/messages/output slice, so a
well-instrumented Inspect run is not under-reported as "insufficient".

Note on live vs post-hoc evidence: during live scoring some evidence is not yet
present (other scorers may not have run, the full event log is still being
written). Reading a *completed* eval log via ``inspect_ai.log.read_eval_log`` and
passing it to ``build_card`` yields the richest card; this live scorer is the
lower-evidence path by construction.
"""

from __future__ import annotations

from typing import Any

from .card import build_card

try:  # pragma: no cover - optional dependency path.
    from inspect_ai.scorer import Score, Target, mean, scorer, stderr
    from inspect_ai.solver import TaskState

    INSPECT_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency path.
    Score = Target = TaskState = None  # type: ignore[assignment]
    INSPECT_AVAILABLE = False


def taskstate_to_trace(state: Any, target: Any = None) -> dict[str, Any]:
    """Map an Inspect ``TaskState`` into the card extractor's trace dict.

    Pure-python and dependency-free: it only reads attributes off ``state`` via
    ``getattr`` so it can be unit-tested without ``inspect-ai`` installed. The
    keys are chosen to match the extractor's field detectors (model, tools,
    scorers, uuid/sample_id, messages, output).

    Empty / absent fields are omitted rather than emitted as ``[]`` / ``None`` /
    ``""`` so the card does not over-count phantom evidence: an empty tool or
    scorer list must read as missing evidence, not partial.
    """
    candidate = {
        "source_kind": "inspect-taskstate",
        "metadata": _jsonable(getattr(state, "metadata", {}) or {}),
        "model": str(getattr(state, "model", "") or ""),
        "input": _jsonable(getattr(state, "input", None)),
        "messages": _jsonable(getattr(state, "messages", []) or []),
        "output": _jsonable(getattr(state, "output", None)),
        "tools": _tool_names(getattr(state, "tools", []) or []),
        "tool_choice": _jsonable(getattr(state, "tool_choice", None)),
        # scorer identity comes from the keys of state.scores ({name: Score}).
        "scorers": _score_names(getattr(state, "scores", None)),
        "scores": _jsonable(getattr(state, "scores", None)),
        "sample_id": getattr(state, "sample_id", None),
        "epoch": getattr(state, "epoch", None),
        "uuid": getattr(state, "uuid", None),
        "completed": getattr(state, "completed", None),
        "target": _jsonable(target),
    }
    return {k: v for k, v in candidate.items() if v not in (None, "", [], {})}


if INSPECT_AVAILABLE:  # pragma: no cover - exercised only when inspect-ai is installed (CI 'inspect' lane).

    @scorer(metrics=[mean(), stderr()])
    def evidence_sufficiency(
        release_decision: str,
        eval_objective: str,
        source_url: str | None = None,
        source_title: str | None = None,
    ):
        async def score(state: TaskState, target: Target) -> Score:
            trace = taskstate_to_trace(state, target)
            card = build_card(
                trace,
                release_decision=release_decision,
                eval_objective=eval_objective,
                source_url=source_url,
                source_title=source_title,
            )
            summary = card["score_summary"]
            return Score(
                value=summary["sufficiency_score"],
                answer=summary["overall_verdict"],
                explanation="; ".join(summary["blocking_gaps"]) or "no blocking gaps",
                metadata={"evidence_sufficiency_card": card},
            )

        return score

else:

    def evidence_sufficiency(*_: Any, **__: Any) -> Any:
        raise RuntimeError("Install optional dependency with: pip install 'inspect-evidence-sufficiency[inspect]'")


def _tool_names(tools: Any) -> list[str]:
    """Best-effort tool names from a list of Inspect tools."""
    tool_def = None
    try:  # pragma: no cover - optional dependency path.
        from inspect_ai.tool import ToolDef as tool_def  # type: ignore[no-redef]
    except Exception:  # pragma: no cover
        tool_def = None
    names: list[str] = []
    for t in tools or []:
        name = None
        if tool_def is not None:
            try:
                name = tool_def(t).name
            except Exception:
                name = None
        if not name:
            name = getattr(t, "__name__", None) or getattr(type(t), "__name__", None)
        if name:
            names.append(str(name))
    return names


def _score_names(scores: Any) -> list[str]:
    """Scorer identity = the keys of the {name: Score} dict."""
    if isinstance(scores, dict):
        return [str(k) for k in scores.keys()]
    return []


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return repr(value)
