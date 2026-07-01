"""Evidence-Sufficiency-Card construction."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .extract import TraceFeatures, extract_features, load_trace

STATUS_WEIGHT = {"present": 1.0, "partial": 0.5, "missing": 0.0}

# A replay/trace handle that a machine can key on: a ``name:value`` identifier
# (``replay:...``, ``trace:...``), an ``@digest`` / ``sha256:`` digest, a UUID,
# or the extractor's hashed-text placeholder for a long opaque value. This is
# deliberately NOT a free-text substring test: a prose sentence that merely
# contains the word "true" or "deterministic" must not qualify, so a fabricated
# free-text replay note cannot certify F09. Mirrors the honest floor F11 keeps.
_STRUCTURED_HANDLE_RE = re.compile(
    r"^(text_sha256:[0-9a-f]{8,}|sha256:[0-9a-f]{8,}|[a-z0-9][a-z0-9._-]*[:@][a-z0-9][a-z0-9._-]*"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


def _has_structured_handle(refs: set[str]) -> bool:
    """True if any ref is a machine-identifiable handle (id/digest/uuid), not prose.

    Used to keep F09 at ``present`` only on a structured replay handle. A free-text
    replay note (with spaces / a sentence) is not a handle and cannot lift F09 above
    ``partial``.
    """
    return any(_STRUCTURED_HANDLE_RE.match(ref.strip()) for ref in refs)


FIELD_LABELS = {
    "F01": "eval objective and release decision",
    "F02": "task and dataset sample scope",
    "F03": "model/provider/resolved version binding",
    "F04": "prompt, scaffold, and agent policy identity",
    "F05": "tools, sandbox, and network permissions",
    "F06": "raw messages, events, and tool calls",
    "F07": "scorer and monitor identity",
    "F08": "retries, errors, and timeouts",
    "F09": "transcript and replay handle",
    "F10": "policy and counterfactual replay notes",
    "F11": "leakage and reward-hacking checks",
    "F12": "residual uncertainty and not-sufficient-for statement",
}


def _resolved_now(now: datetime | None) -> datetime:
    """Resolve the card timestamp: explicit `now`, else SOURCE_DATE_EPOCH, else current UTC."""
    if now is not None:
        return now if now.tzinfo else now.replace(tzinfo=UTC)
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            return datetime.fromtimestamp(int(epoch), tz=UTC)
        except (ValueError, OSError, OverflowError):
            pass
    return datetime.now(UTC)


def build_card(
    trace: str | Path | Any,
    *,
    release_decision: str | None = None,
    eval_objective: str | None = None,
    source_url: str | None = None,
    source_title: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build an Evidence-Sufficiency-Card.

    `trace` may be a path to JSON/JSONL, a ControlArena export directory, or an
    in-memory JSON-like object.

    The card is deterministic given fixed inputs and a fixed timestamp. Pass
    `now`, or set `SOURCE_DATE_EPOCH`, to pin `created_utc` for byte-reproducible
    output; otherwise the current UTC time is stamped.
    """
    if isinstance(trace, (str, Path)):
        data, features = load_trace(trace)
    else:
        data = trace
        features = extract_features(data)

    release_decision = release_decision or _metadata_value(data, "release_decision")
    eval_objective = eval_objective or _metadata_value(data, "eval_objective")
    source_title = source_title or _metadata_value(data, "source_title") or "agent eval trace"

    fields = _assess_fields(features, release_decision, eval_objective)
    score = round(sum(STATUS_WEIGHT[f["status"]] for f in fields) / len(fields), 3)
    blocking_gaps = _blocking_gaps(fields)
    verdict = _verdict(score, blocking_gaps)

    return {
        "schema": "evidence-sufficiency-card.v0",
        "version": 1,
        "created_utc": _resolved_now(now).replace(microsecond=0).isoformat(),
        "data_safety": {
            "mode": "trace-derived",
            "note": (
                "Derived from supplied trace/log fields. The card reports evidence presence, "
                "not model quality, benchmark validity, safety, compliance, or legal sufficiency."
            ),
        },
        "source": {
            "title": source_title,
            "url": source_url,
            "path": features.source_path,
            "sha256": features.source_sha256,
            "kind": features.source_kind,
        },
        "eval_run_context": {
            "release_decision": release_decision or "",
            "eval_objective": eval_objective or "",
            "release_use_boundary": (
                "Use this card to inspect whether an eval result can support a named decision. "
                "Do not treat the score alone as release approval."
            ),
        },
        "trace_features": features.to_summary(),
        "score_summary": {
            "overall_verdict": verdict,
            "sufficiency_score": score,
            "status_counts": _status_counts(fields),
            "blocking_gaps": blocking_gaps,
            "sufficient_for": _sufficient_for(verdict),
            "not_sufficient_for": [
                "production release approval by itself",
                "customer-facing reliability or safety claim",
                "benchmark leaderboard claim",
                "audit, legal, or compliance sufficiency",
            ],
        },
        "card_fields": fields,
        "release_gate_verdict": {
            "decision": _decision_text(verdict, blocking_gaps),
            "one_sentence_card_claim": _one_sentence(verdict),
        },
        "claim_boundary": {
            "allowed": [
                "The card reports which evidence fields are present, partial, or missing.",
                "The card can be used as a post-hoc evidence sufficiency probe over "
                "Inspect-style logs, ControlArena trajectory exports, or generic JSON/JSONL agent traces.",
            ],
            "avoid": [
                "Do not claim Inspect or ControlArena lack logs or trajectories.",
                "Do not claim upstream acceptance unless a public upstream issue/PR is accepted.",
                "Do not claim model, benchmark, or control-protocol safety.",
                "Do not claim audit, compliance, or legal sufficiency.",
                "Do not read F09/F10 as verified replay: they reflect a declared replay handle and "
                "counterfactual claim at face value and are not executed or verified by this card.",
            ],
        },
    }


def summarize_card(card: dict[str, Any]) -> str:
    summary = card["score_summary"]
    lines = [
        f"verdict: {summary['overall_verdict']}",
        f"sufficiency_score: {summary['sufficiency_score']}",
        f"status_counts: {summary['status_counts']}",
        "blocking_gaps:",
    ]
    gaps = summary.get("blocking_gaps") or []
    lines.extend(f"- {gap}" for gap in gaps)
    if not gaps:
        lines.append("- none")
    return "\n".join(lines)


def _assess_fields(
    f: TraceFeatures,
    release_decision: str | None,
    eval_objective: str | None,
) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []

    def add(field_id: str, status: str, handle: str, finding: str, implication: str) -> None:
        fields.append(
            {
                "id": field_id,
                "field": FIELD_LABELS[field_id],
                "status": status,
                "evidence_handle": handle,
                "finding": finding,
                "release_implication": implication,
            }
        )

    has_release_context = bool(release_decision and eval_objective)
    add(
        "F01",
        "present" if has_release_context else "missing",
        "eval_run_context.release_decision + eval_run_context.eval_objective" if has_release_context else "",
        "The card names the decision and objective."
        if has_release_context
        else "No named release decision and eval objective were found or supplied.",
        "A pass/fail eval score cannot become decision evidence unless the decision it informs is named.",
    )

    if f.sample_count and (f.task_refs or f.dataset_refs):
        scope_status = "present"
        scope_finding = "Task/sample scope is visible in the trace metadata."
    elif f.sample_count or f.task_refs:
        scope_status = "partial"
        scope_finding = "Some task or sample evidence is visible, but dataset/coverage scope is incomplete."
    else:
        scope_status = "missing"
        scope_finding = "No task, dataset, or sample scope was identified."
    add(
        "F02",
        scope_status,
        _join_refs(f.task_refs | f.dataset_refs) or "trace_features.sample_count",
        scope_finding,
        "Scope limits determine whether an eval result can support a release claim.",
    )

    if f.resolved_model_refs:
        model_status = "present"
        model_finding = "A model/provider reference appears resolved by digest, date, release, or version string."
    elif f.model_refs:
        model_status = "partial"
        model_finding = "Model references exist, but no resolved provider snapshot/digest was identified."
    else:
        model_status = "missing"
        model_finding = "No model/provider reference was identified."
    add(
        "F03",
        model_status,
        _join_refs(f.resolved_model_refs or f.model_refs),
        model_finding,
        "Unresolved aliases block bit-faithful reproduction claims.",
    )

    if f.prompt_refs and f.policy_refs:
        prompt_status = "present"
        prompt_finding = "Prompt/scaffold evidence and policy identity are both visible."
    elif f.prompt_refs or f.policy_refs:
        prompt_status = "partial"
        prompt_finding = "Prompt/scaffold or policy identity is visible, but not both."
    else:
        prompt_status = "missing"
        prompt_finding = "No prompt/scaffold or policy identity was identified."
    add(
        "F04",
        prompt_status,
        _join_refs(f.prompt_refs | f.policy_refs),
        prompt_finding,
        "The agent instruction and policy surface must be known to reconstruct the run.",
    )

    if f.permission_refs and f.tool_call_count:
        perm_status = (
            "present"
            if any("sandbox" in r.lower() or "network" in r.lower() or "allow" in r.lower() for r in f.permission_refs)
            else "partial"
        )
        perm_finding = "Tool or permission evidence is visible."
    else:
        perm_status = "missing"
        perm_finding = "No tool/permission evidence was identified."
    add(
        "F05",
        perm_status,
        _join_refs(f.permission_refs),
        perm_finding,
        "Tool, sandbox, and network boundaries shape what the eval actually tested.",
    )

    if f.message_count and (f.event_count or f.tool_call_count):
        raw_status = "present"
        raw_finding = "Messages plus events or tool calls are present."
    elif f.message_count or f.event_count or f.tool_call_count:
        raw_status = "partial"
        raw_finding = "Only part of the transcript/event/tool-call surface is visible."
    else:
        raw_status = "missing"
        raw_finding = "No transcript, event, or tool-call rows were identified."
    add(
        "F06",
        raw_status,
        "trace_features.message_count/event_count/tool_call_count",
        raw_finding,
        "Raw run evidence is the debugging and reconstruction substrate.",
    )

    if f.scorer_refs and (f.score_count or f.monitor_refs):
        scorer_status = "present"
        scorer_finding = "Scorer or monitor identity is visible with scores."
    elif f.score_count or f.scorer_refs or f.monitor_refs:
        scorer_status = "partial"
        scorer_finding = "Scores or scorer/monitor names exist, but the full scorer manifest is not visible."
    else:
        scorer_status = "missing"
        scorer_finding = "No scorer or monitor identity was identified."
    add(
        "F07",
        scorer_status,
        _join_refs(f.scorer_refs | f.monitor_refs),
        scorer_finding,
        "A score is interpretable only as far as the scorer/monitor definition is reviewable.",
    )

    if f.error_count or f.retry_count or f.timeout_count:
        rt_status = "present"
        rt_finding = "Retry/error/timeout evidence is visible."
    elif f.event_count:
        rt_status = "partial"
        rt_finding = "Events are visible, but explicit retry/error/timeout counters were not identified."
    else:
        rt_status = "missing"
        rt_finding = "No retry/error/timeout evidence was identified."
    add(
        "F08",
        rt_status,
        "trace_features.error_count/retry_count/timeout_count",
        rt_finding,
        "Hidden instability can invalidate a clean-looking score.",
    )

    # F09 reaches `present` only on a STRUCTURED, machine-identifiable replay
    # handle (a replay/trace id, digest, or uuid) — never on a free-text substring
    # such as the word "true" inside a prose replay note. A fabricated free-text
    # replay claim therefore stays `partial`, so it cannot certify reconstruction.
    if _has_structured_handle(f.replay_refs) and (f.trace_ids or f.decision_ids):
        replay_status = "present"
        replay_finding = (
            "A structured trace/replay handle (id or digest) is visible alongside a "
            "trace/decision id. This reflects a declared replay handle at face value; "
            "the card does not itself execute or verify a deterministic replay."
        )
    elif f.replay_refs or f.trace_ids or f.decision_ids:
        replay_status = "partial"
        replay_finding = (
            "Trace, decision, or replay handles exist, but no structured replay handle "
            "establishes deterministic replay; free-text replay notes are not sufficient."
        )
    else:
        replay_status = "missing"
        replay_finding = "No trace/replay handle was identified."
    add(
        "F09",
        replay_status,
        _join_refs(f.replay_refs | f.trace_ids | f.decision_ids),
        replay_finding,
        "Observed-run reconstruction is weaker than bit-faithful replay unless determinism is proven.",
    )

    # F10 is capped at `partial`: the extractor flattens a counterfactual entry to
    # a bare string, so a genuine executed-outcome record cannot be cleanly told
    # apart from a prose sentence that merely contains the words "executed"/"result".
    # Rather than let free text certify F10, the honest floor is `partial` (mirroring
    # F11) — the presence of a declared counterfactual is reported, never verified.
    # `present` would require a structured, verified counterfactual-outcome surface
    # this card does not yet consume.
    if f.counterfactual_refs:
        cf_status = "partial"
        cf_finding = (
            "A counterfactual replay is declared or mentioned in the trace, but this "
            "card neither executes nor verifies it; a declared executed result is "
            "reported at face value and is not independently confirmed."
        )
    else:
        cf_status = "missing"
        cf_finding = "No counterfactual replay evidence was identified."
    add(
        "F10",
        cf_status,
        _join_refs(f.counterfactual_refs),
        cf_finding,
        "Without counterfactual replay, alternate-policy claims remain unsupported.",
    )

    if f.leakage_refs:
        leak_status = "partial"
        leak_finding = (
            "Leakage/anonymization/reward-hacking evidence is mentioned in the trace, but this "
            "card did not run an independent leakage or reward-hacking scanner."
        )
    else:
        leak_status = "missing"
        leak_finding = "No leakage, contamination, or reward-hacking check was identified."
    add(
        "F11",
        leak_status,
        _join_refs(f.leakage_refs),
        leak_finding,
        "Benchmark/eval integrity risk must remain explicit when not independently checked.",
    )

    add(
        "F12",
        "present",
        "score_summary.not_sufficient_for + claim_boundary.avoid",
        "The card explicitly lists what the eval evidence cannot support.",
        "This prevents score-to-release overclaiming.",
    )
    return fields


def _blocking_gaps(fields: list[dict[str, str]]) -> list[str]:
    critical = {"F01", "F02", "F03", "F06", "F07", "F09", "F10", "F12"}
    gaps = []
    for field in fields:
        if field["id"] in critical and field["status"] != "present":
            gaps.append(f"{field['id']} {field['field']}: {field['status']}")
    return gaps


def _verdict(score: float, blocking_gaps: list[str]) -> str:
    if score >= 0.85 and not blocking_gaps:
        return "sufficient-for-named-decision"
    if score < 0.5:
        return "insufficient"
    return "conditional"


def _decision_text(verdict: str, blocking_gaps: list[str]) -> str:
    if verdict == "sufficient-for-named-decision":
        return "The evidence packet may support the named decision, subject to normal human review."
    if verdict == "insufficient":
        return "Do not use this eval result as release evidence. Build the missing evidence packet first."
    return "Use this eval result for review/debugging only until the blocking gaps are resolved: " + "; ".join(
        blocking_gaps
    )


def _one_sentence(verdict: str) -> str:
    if verdict == "sufficient-for-named-decision":
        return "The eval evidence is sufficient for the named decision, within the card's stated boundary."
    if verdict == "insufficient":
        return "The eval may have run, but the evidence packet is not sufficient for a release decision."
    return (
        "The eval evidence is conditional: it reconstructs parts of the observed run, "
        "but not the full release decision."
    )


def _status_counts(fields: list[dict[str, str]]) -> dict[str, int]:
    return {status: sum(1 for f in fields if f["status"] == status) for status in ("present", "partial", "missing")}


def _sufficient_for(verdict: str) -> list[str]:
    if verdict == "sufficient-for-named-decision":
        return ["named engineering review or gate decision, within the stated boundary"]
    if verdict == "conditional":
        return ["debugging", "engineering review", "planning follow-up evidence capture"]
    return ["triage only", "identifying missing evidence"]


def _join_refs(refs: set[str], limit: int = 5) -> str:
    return "; ".join(sorted(refs)[:limit])


def _metadata_value(data: Any, key: str) -> str | None:
    if isinstance(data, dict):
        for container in (
            data.get("metadata"),
            data.get("eval", {}).get("metadata") if isinstance(data.get("eval"), dict) else None,
        ):
            if isinstance(container, dict) and isinstance(container.get(key), str):
                return container[key]
    return None
