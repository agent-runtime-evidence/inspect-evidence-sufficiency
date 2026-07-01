"""Monitor-Coverage-Card construction.

Where the Evidence-Sufficiency-Card asks *is a scorer/monitor identity present*,
this card asks the next question: *did an identified monitor actually score the
turns that executed the risky tools?* Presence is not coverage.

Coverage is not accuracy. This card checks only whether an identified monitor
**observed** (produced a suspicion score for) the assistant turn that ran a risky
tool. It says nothing about whether the monitor's judgment was correct: a
99%-catch-rate monitor that was never invoked on the risky turn has zero coverage
here, and a monitor that scored every turn but scored them all wrong still reads
as fully covered. Judging the monitor's verdict is a separate, out-of-scope
question.

Input shape ("monitor-coverage trace")
--------------------------------------
A JSON list of messages, or ``{"monitor": "<name>", "messages": [...]}``. Each
message mirrors the real Inspect message the scores live on::

    {"role": "assistant",
     "content": "...",
     "tool_calls": [{"function": "transfer", "arguments": {...}, "id": "c1"}],
     "metadata": {"suspicion_score": 0.2, "reason": "..."}}

    {"role": "tool", "tool_call_id": "c1", "content": "..."}

An assistant turn is *monitored* iff its ``metadata.suspicion_score`` is a number.
A per-turn monitor scores the assistant TURN, not each call: N tool calls in one
assistant message share the single score on that message. Monitor identity is a
run-level property (a declared ``monitor`` field and/or a scorer name), never
inferred per message.

The card is byte-reproducible given fixed inputs and a fixed timestamp; pass
``now=`` or set ``SOURCE_DATE_EPOCH`` to pin ``created_utc``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "monitor-coverage-card.v0"

BOUNDARY = (
    "coverage = whether an identified monitor scored the turn that executed the risky tool; "
    "NOT whether the monitor's judgment was correct."
)

# Verdict strings owned here. The CLI re-derives its gate mapping from these; this
# module remains the source of truth for how a verdict is assigned.
VERDICT_COVERED = "covered"
VERDICT_PARTIAL = "partial-coverage"
VERDICT_UNCOVERED = "uncovered"


class MonitorCoverageError(ValueError):
    """A trace that cannot be read as a monitor-coverage trace.

    Raised for a scalar top level, a message that is not an object, or a
    ``tool_call_id`` that references no known tool-call id. The CLI maps this to a
    usage error (exit 2) so it is never mistaken for a gate block.
    """


def load_coverage_trace(path: str | Path) -> tuple[str | None, list[dict[str, Any]], str]:
    """Load a JSON/JSONL monitor-coverage trace from ``path``.

    Returns ``(monitor_name, messages, source_kind)``. A ``.jsonl`` file is read
    as one message per line; a ``.json`` file is either a list of messages or a
    ``{"monitor": ..., "messages": [...]}`` object.
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    if p.suffix == ".jsonl":
        data: Any = [json.loads(line) for line in raw.splitlines() if line.strip()]
        kind = "jsonl-monitor-coverage-trace"
    else:
        data = json.loads(raw)
        kind = "json-monitor-coverage-trace"
    monitor, messages = _normalize_trace(data)
    return monitor, messages, kind


def _normalize_trace(data: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """Coerce a parsed trace into ``(monitor_name, messages)`` or raise.

    Accepts a bare list of messages or a ``{"monitor", "messages"}`` object. A
    scalar top level, or a non-object message, is a usage error.
    """
    if isinstance(data, list):
        monitor: str | None = None
        messages = data
    elif isinstance(data, dict):
        monitor = _clean_name(data.get("monitor"))
        raw_messages = data.get("messages")
        if raw_messages is None:
            raise MonitorCoverageError("trace object has no 'messages' list")
        if not isinstance(raw_messages, list):
            raise MonitorCoverageError("'messages' must be a list")
        messages = raw_messages
    else:
        raise MonitorCoverageError(
            f"trace top level must be a JSON array of messages or an object with a "
            f"'messages' list, not {type(data).__name__}"
        )

    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            raise MonitorCoverageError(f"message at index {i} is not a JSON object")
    _check_tool_call_ids(messages)
    return monitor, messages


def _check_tool_call_ids(messages: list[dict[str, Any]]) -> None:
    """Every ``tool`` message's ``tool_call_id`` must reference a known tool call.

    A dangling ``tool_call_id`` (a tool result with no matching assistant
    ``tool_calls[].id``) is a malformed trace, surfaced as a usage error (exit 2)
    rather than silently ignored. Tool messages without a ``tool_call_id`` at all
    are tolerated (the id is optional metadata, not the coverage signal).
    """
    known_ids: set[str] = set()
    for m in messages:
        for call in m.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("id") is not None:
                known_ids.add(str(call["id"]))
    for i, m in enumerate(messages):
        if m.get("role") == "tool":
            ref = m.get("tool_call_id")
            if ref is not None and str(ref) not in known_ids:
                raise MonitorCoverageError(
                    f"tool message at index {i} has tool_call_id {ref!r} that matches no tool call id"
                )


def build_coverage_card(
    trace: str | Path | list[dict[str, Any]] | dict[str, Any],
    *,
    risky_tools: list[str] | None = None,
    monitor_name: str | None = None,
    source_url: str | None = None,
    source_title: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a Monitor-Coverage-Card.

    ``trace`` is a path to JSON/JSONL, a list of messages, or a
    ``{"monitor", "messages"}`` object. ``risky_tools`` is the team-owned policy:
    the set of tool names whose executing turns must be monitored. When it is
    ``None`` or empty, every tool-bearing turn is treated as risky ("all-tools").
    ``monitor_name`` overrides / supplies the run-level monitor identity.
    """
    source_kind = "in-memory-monitor-coverage-trace"
    if isinstance(trace, (str, Path)):
        declared_monitor, messages, source_kind = load_coverage_trace(trace)
        source_path: str | None = str(Path(trace))
    else:
        declared_monitor, messages = _normalize_trace(trace)
        source_path = None

    monitor = _clean_name(monitor_name) or declared_monitor
    risky_set = [t for t in (risky_tools or []) if t]
    turns = _collect_turns(messages, risky_set)

    tool_turns = [t for t in turns if t["tools"]]
    risky_turns = [t for t in turns if t["is_risky"]]
    monitored_risky = [t for t in risky_turns if t["monitored"]]

    coverage_ratio = round(len(monitored_risky) / len(risky_turns), 3) if risky_turns else 1.0
    uncovered = [{"turn_index": t["turn_index"], "tools": t["risky_tools"]} for t in risky_turns if not t["monitored"]]

    monitor_present = bool(monitor)
    verdict = _verdict(monitor_present, coverage_ratio)

    return {
        "schema": SCHEMA,
        "version": 1,
        "created_utc": _resolved_now(now).replace(microsecond=0).isoformat(),
        "data_safety": {
            "mode": "trace-derived",
            "note": (
                "Derived from the supplied monitor-coverage trace. The card reports whether an "
                "identified monitor scored the risky tool turns, not whether the monitor was correct, "
                "and not model quality, safety, or compliance."
            ),
        },
        "source": {
            "title": source_title or "monitor-coverage trace",
            "url": source_url,
            "path": source_path,
            "kind": source_kind,
        },
        "monitor_identity": {"present": monitor_present, "name": monitor or ""},
        "risky_set": _risky_set_summary(risky_set),
        "tool_turn_count": len(tool_turns),
        "risky_turn_count": len(risky_turns),
        "monitored_risky_turn_count": len(monitored_risky),
        "coverage_ratio": coverage_ratio,
        "uncovered_spans": uncovered,
        "verdict": verdict,
        "verdict_reason": _verdict_reason(verdict, monitor_present, coverage_ratio, len(risky_turns)),
        "boundary": BOUNDARY,
        "claim_boundary": {
            "allowed": [
                "The card reports whether an identified monitor produced a suspicion score for each risky tool turn.",
                "The card can flag risky tool executions that no identified monitor scored (a coverage gap).",
            ],
            "avoid": [
                "Do not read coverage as accuracy: a scored turn says the monitor looked, not that it was right.",
                "Do not read this as a claim that ControlArena or any monitor is broken; a normal run scores "
                "every turn and cards as covered. The gap appears under partial, sampled, or post-hoc monitoring, "
                "or when a trace names a monitor but carries no per-turn scores.",
                "Do not read a covered verdict as a safety, quality, or compliance approval, or as release approval.",
            ],
        },
    }


def summarize_coverage_card(card: dict[str, Any]) -> str:
    """A short human-readable summary of a Monitor-Coverage-Card."""
    identity = card.get("monitor_identity", {})
    name = identity.get("name") or "(none)"
    lines = [
        f"verdict: {card.get('verdict', '')}",
        f"monitor_identity: present={identity.get('present', False)} name={name}",
        f"coverage_ratio: {card.get('coverage_ratio', '')}",
        f"risky_turns: {card.get('risky_turn_count', 0)} "
        f"(monitored {card.get('monitored_risky_turn_count', 0)}, tool_turns {card.get('tool_turn_count', 0)})",
        "uncovered_spans:",
    ]
    spans = card.get("uncovered_spans") or []
    lines.extend(f"- turn {span['turn_index']}: {', '.join(span['tools']) or '(tools)'}" for span in spans)
    if not spans:
        lines.append("- none")
    return "\n".join(lines)


def _collect_turns(messages: list[dict[str, Any]], risky_set: list[str]) -> list[dict[str, Any]]:
    """Reduce raw messages to one record per assistant turn, in order.

    Each record: ``turn_index`` (0-based over assistant turns), ``tools`` (all
    tool names called in that turn), ``risky_tools`` (the subset in the risky
    policy, or all tools when the policy is "all-tools"), ``is_risky``, and
    ``monitored`` (a numeric ``metadata.suspicion_score`` is present).
    """
    risky_lookup = {t.lower() for t in risky_set}
    all_tools_are_risky = not risky_lookup

    turns: list[dict[str, Any]] = []
    turn_index = 0
    for m in messages:
        if m.get("role") != "assistant":
            continue
        tools = _tool_names(m.get("tool_calls"))
        risky_tools = tools if all_tools_are_risky else [t for t in tools if t.lower() in risky_lookup]
        turns.append(
            {
                "turn_index": turn_index,
                "tools": tools,
                "risky_tools": risky_tools,
                "is_risky": bool(risky_tools),
                "monitored": _is_monitored(m),
            }
        )
        turn_index += 1
    return turns


def _tool_names(tool_calls: Any) -> list[str]:
    """Ordered tool names from a message's ``tool_calls`` list.

    Reads the ``function`` key (the real Inspect ``ToolCall`` serialized form),
    falling back to ``name``/``tool``. Malformed / non-list ``tool_calls`` yield no
    tools rather than raising, so a cosmetically broken row degrades to "no risky
    tool here", never a crash.
    """
    if not isinstance(tool_calls, list):
        return []
    names: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        name = call.get("function") or call.get("name") or call.get("tool")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def _is_monitored(message: dict[str, Any]) -> bool:
    """True iff the assistant message carries a numeric ``metadata.suspicion_score``.

    A boolean is not a score (``bool`` is an ``int`` subclass in Python, so it is
    excluded explicitly); a string, ``None``, or a missing key all read as not
    monitored.
    """
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        return False
    score = metadata.get("suspicion_score")
    return isinstance(score, (int, float)) and not isinstance(score, bool)


def _verdict(monitor_present: bool, coverage_ratio: float) -> str:
    """``uncovered`` if no identified monitor or zero coverage; ``covered`` at full
    coverage with an identified monitor; ``partial-coverage`` otherwise."""
    if not monitor_present or coverage_ratio == 0.0:
        return VERDICT_UNCOVERED
    if coverage_ratio == 1.0:
        return VERDICT_COVERED
    return VERDICT_PARTIAL


def _verdict_reason(verdict: str, monitor_present: bool, coverage_ratio: float, risky_turns: int) -> str:
    if verdict == VERDICT_COVERED:
        if risky_turns == 0:
            return "An identified monitor is declared and no risky tool turn was found to leave uncovered."
        return "An identified monitor scored every risky tool turn (coverage 1.0)."
    if verdict == VERDICT_UNCOVERED:
        if not monitor_present:
            return "No identified monitor: coverage cannot be established without a named monitor/scorer."
        return "An identified monitor scored none of the risky tool turns (coverage 0.0)."
    return f"An identified monitor scored some but not all risky tool turns (coverage {coverage_ratio})."


def _risky_set_summary(risky_set: list[str]) -> Any:
    return sorted(risky_set) if risky_set else "all-tools"


def _clean_name(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resolved_now(now: datetime | None) -> datetime:
    """Resolve the card timestamp: explicit ``now``, else ``SOURCE_DATE_EPOCH``, else current UTC."""
    if now is not None:
        return now if now.tzinfo else now.replace(tzinfo=UTC)
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            return datetime.fromtimestamp(int(epoch), tz=UTC)
        except (ValueError, OSError, OverflowError):
            pass
    return datetime.now(UTC)
