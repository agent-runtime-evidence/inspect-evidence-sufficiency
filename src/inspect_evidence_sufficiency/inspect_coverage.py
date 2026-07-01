"""Optional Inspect ``.eval`` reader for the Monitor-Coverage-Card.

The per-step monitor suspicion scores this card needs live on the assistant
message inside a completed Inspect eval log — ``ChatMessageAssistant.metadata``
carries ``suspicion_score`` (a float 0-1) and ``reason``, co-located on the SAME
message that carries ``tool_calls``. They are written into the ``.eval`` log but
stripped by the ControlArena ``trajectory export`` formatters, so this reader goes
to the ``.eval`` log directly via ``inspect_ai.log.read_eval_log``.

Only imported when used. The core ``coverage`` module stays stdlib-only; this
module is behind the ``[inspect]`` extra, exactly like ``inspect_adapter``.

``eval_log_to_coverage_trace`` is pure-python and dependency-free (it only reads
attributes off the passed objects via ``getattr``), so the mapping is unit-tested
without ``inspect-ai`` installed. ``read_eval_log_coverage`` is the thin wrapper
that actually opens a ``.eval`` file and requires the extra.

Field notes (re-verify against your installed ``inspect_ai`` before relying on
exact shapes):

- ``read_eval_log(path).samples`` is a list of ``EvalSample``; each has
  ``.messages`` (the chat transcript) and ``.scores`` (``{name: Score}``).
- An assistant message is ``ChatMessageAssistant`` with ``.role == "assistant"``,
  ``.tool_calls`` (a list of ``ToolCall``, each with ``.function`` / ``.arguments``
  / ``.id`` and an extra ``internal`` passthrough key in its serialized form), and
  ``.metadata`` (a dict; per-step monitors write ``suspicion_score`` here).
- Monitor identity: prefer the sample/scorer name(s) in ``sample.scores``, else a
  declared monitor in ``log.eval.metadata`` / ``sample.metadata``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .coverage import build_coverage_card


def read_eval_log_coverage(
    path: str | Path,
    *,
    risky_tools: list[str] | None = None,
    sample_index: int = 0,
    monitor_name: str | None = None,
    source_url: str | None = None,
    source_title: str | None = None,
) -> dict[str, Any]:
    """Read one sample of a real Inspect ``.eval`` log and build a coverage card.

    Requires the optional ``[inspect]`` extra. ``sample_index`` selects which
    sample's message transcript to score (default: the first). Raises
    ``RuntimeError`` with an install hint if ``inspect-ai`` is not available.
    """
    try:
        from inspect_ai.log import read_eval_log
    except Exception as exc:  # pragma: no cover - optional dependency path.
        raise RuntimeError(
            "Reading an Inspect .eval log needs the optional dependency: "
            "pip install 'inspect-evidence-sufficiency[inspect]'"
        ) from exc

    log = read_eval_log(str(path))
    trace = eval_log_to_coverage_trace(log, sample_index=sample_index)
    return build_coverage_card(
        trace,
        risky_tools=risky_tools,
        monitor_name=monitor_name,
        source_url=source_url,
        source_title=source_title or "Inspect .eval log (monitor coverage)",
    )


def eval_log_to_coverage_trace(log: Any, *, sample_index: int = 0) -> dict[str, Any]:
    """Map an Inspect ``EvalLog`` into the core monitor-coverage trace shape.

    Pure-python: reads only via ``getattr`` / ``dict`` access, so it is unit-tested
    with duck-typed fakes. Returns ``{"monitor": <name|None>, "messages": [...]}``
    where each assistant message keeps its ``tool_calls`` (mapped to
    ``{"function", "arguments", "id"}``) and its ``metadata`` (so a co-located
    ``suspicion_score`` survives into the card).
    """
    samples = getattr(log, "samples", None) or []
    sample = samples[sample_index] if len(samples) > sample_index else None

    messages_out: list[dict[str, Any]] = []
    if sample is not None:
        for message in getattr(sample, "messages", None) or []:
            messages_out.append(_message_to_trace(message))

    return {
        "monitor": _monitor_identity(log, sample),
        "messages": messages_out,
    }


def _message_to_trace(message: Any) -> dict[str, Any]:
    """Map one Inspect chat message to the core trace message dict.

    Only assistant messages carry ``tool_calls`` / ``metadata`` that matter for
    coverage; other roles are passed through with role + a best-effort id so the
    turn ordering is preserved.
    """
    role = _attr(message, "role")
    out: dict[str, Any] = {"role": role}

    if role == "assistant":
        tool_calls = _attr(message, "tool_calls")
        mapped_calls = [_tool_call_to_trace(tc) for tc in (tool_calls or [])]
        if mapped_calls:
            out["tool_calls"] = mapped_calls
        metadata = _attr(message, "metadata")
        if isinstance(metadata, dict) and metadata:
            out["metadata"] = _jsonable(metadata)
    else:
        tool_call_id = _attr(message, "tool_call_id")
        if tool_call_id is not None:
            out["tool_call_id"] = tool_call_id
    return out


def _tool_call_to_trace(tool_call: Any) -> dict[str, Any]:
    """Map an Inspect ``ToolCall`` to ``{"function", "arguments", "id"}``.

    The serialized ``ToolCall`` also carries an extra ``internal`` passthrough key;
    it is not needed for coverage (the tool name + id suffice) and is dropped.
    """
    return {
        "function": _attr(tool_call, "function"),
        "arguments": _jsonable(_attr(tool_call, "arguments")) or {},
        "id": _attr(tool_call, "id"),
    }


def _monitor_identity(log: Any, sample: Any) -> str | None:
    """Best-effort run-level monitor identity name.

    Prefers a scorer name from ``sample.scores`` (``{name: Score}``), then a
    declared ``monitor`` in ``sample.metadata`` or ``log.eval.metadata``.
    """
    scores = _attr(sample, "scores") if sample is not None else None
    if isinstance(scores, dict) and scores:
        return ", ".join(str(k) for k in scores.keys())

    for container in (
        _attr(sample, "metadata") if sample is not None else None,
        _attr(_attr(log, "eval"), "metadata"),
    ):
        if isinstance(container, dict):
            monitor = container.get("monitor")
            if isinstance(monitor, str) and monitor.strip():
                return monitor.strip()
    return None


def _attr(obj: Any, name: str) -> Any:
    """Read ``name`` from an object attribute or a dict key; ``None`` if absent."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


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
