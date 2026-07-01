"""Trace loading and conservative feature extraction."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

RESOLVED_MODEL_RE = re.compile(
    r"(sha256:|@[a-f0-9]{8,}|snapshot|release|20[2-9][0-9]-[01][0-9]-[0-3][0-9]|v\d+(\.\d+)*)",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Maximum JSON nesting depth the extractor will traverse. Real Inspect /
# ControlArena traces nest a few dozen levels at most; anything deeper is
# pathological or adversarial input. Exceeding it raises ``TraceTooDeepError`` (a
# ``ValueError``), which the CLI maps to a usage error (exit 2) — a clean, bounded
# outcome instead of an unbounded traversal or a ``RecursionError`` traceback.
MAX_TRACE_DEPTH = 512


class TraceTooDeepError(ValueError):
    """Raised when a trace nests deeper than ``MAX_TRACE_DEPTH``."""


def _key_tokens(key: str) -> set[str]:
    """Split a JSON key into lowercased word tokens (snake_case + camelCase).

    Field detection uses token-set membership instead of raw substring matching,
    so ``tool_call`` and ``data_model`` match while ``toolbar`` and ``remodel`` do
    not — the extractor is harder to fool with cosmetic key names.
    """
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return set(_TOKEN_RE.findall(spaced.lower()))


@dataclass
class TraceFeatures:
    source_path: str | None = None
    source_sha256: str | None = None
    source_kind: str = "generic-json"
    top_level_status: str | None = None
    sample_count: int = 0
    record_count: int = 0
    message_count: int = 0
    event_count: int = 0
    tool_call_count: int = 0
    score_count: int = 0
    monitor_count: int = 0
    error_count: int = 0
    retry_count: int = 0
    timeout_count: int = 0
    model_refs: set[str] = field(default_factory=set)
    resolved_model_refs: set[str] = field(default_factory=set)
    prompt_refs: set[str] = field(default_factory=set)
    policy_refs: set[str] = field(default_factory=set)
    permission_refs: set[str] = field(default_factory=set)
    decision_ids: set[str] = field(default_factory=set)
    trace_ids: set[str] = field(default_factory=set)
    replay_refs: set[str] = field(default_factory=set)
    counterfactual_refs: set[str] = field(default_factory=set)
    leakage_refs: set[str] = field(default_factory=set)
    scorer_refs: set[str] = field(default_factory=set)
    monitor_refs: set[str] = field(default_factory=set)
    task_refs: set[str] = field(default_factory=set)
    dataset_refs: set[str] = field(default_factory=set)

    def to_summary(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "source_kind": self.source_kind,
            "top_level_status": self.top_level_status,
            "sample_count": self.sample_count,
            "record_count": self.record_count,
            "message_count": self.message_count,
            "event_count": self.event_count,
            "tool_call_count": self.tool_call_count,
            "score_count": self.score_count,
            "monitor_count": self.monitor_count,
            "error_count": self.error_count,
            "retry_count": self.retry_count,
            "timeout_count": self.timeout_count,
            "model_refs": sorted(self.model_refs),
            "resolved_model_refs": sorted(self.resolved_model_refs),
            "prompt_refs": sorted(self.prompt_refs),
            "policy_refs": sorted(self.policy_refs),
            "permission_refs": sorted(self.permission_refs),
            "decision_ids": sorted(self.decision_ids),
            "trace_ids": sorted(self.trace_ids),
            "replay_refs": sorted(self.replay_refs),
            "counterfactual_refs": sorted(self.counterfactual_refs),
            "leakage_refs": sorted(self.leakage_refs),
            "scorer_refs": sorted(self.scorer_refs),
            "monitor_refs": sorted(self.monitor_refs),
            "task_refs": sorted(self.task_refs),
            "dataset_refs": sorted(self.dataset_refs),
        }


def load_trace(path: str | Path) -> tuple[Any, TraceFeatures]:
    """Load JSON, JSONL, or a ControlArena export directory."""
    p = Path(path)
    if p.is_dir():
        from .controlarena_adapter import load_controlarena_export

        data = load_controlarena_export(p)
        features = extract_features(data)
        features.source_path = str(p)
        features.source_kind = "controlarena-export"
        return data, features

    raw = p.read_bytes()
    text = raw.decode("utf-8")
    if p.suffix == ".jsonl":
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
        kind = "jsonl-agent-trace"
    else:
        data = json.loads(text)
        kind = "json-agent-trace"

    if not isinstance(data, (dict, list)):
        # A bare JSON scalar (null / number / string) is not a trace. Surface it as
        # a usage error (exit 2 via the CLI's ValueError catch) rather than silently
        # scoring an empty, insufficient card.
        raise ValueError(f"trace top level must be a JSON object or array, not {type(data).__name__}")

    features = extract_features(data)
    features.source_path = str(p)
    features.source_sha256 = sha256(raw).hexdigest()
    features.source_kind = kind
    return data, features


def extract_features(data: Any) -> TraceFeatures:
    f = TraceFeatures()
    if isinstance(data, list):
        f.record_count = len(data)
    elif isinstance(data, dict):
        f.record_count = 1
        f.top_level_status = _string_or_none(data.get("status"))
        samples = data.get("samples")
        if isinstance(samples, list):
            f.sample_count = len(samples)
        eval_block = data.get("eval")
        if isinstance(eval_block, dict):
            _collect_named_ref(eval_block.get("task"), f.task_refs)
            _collect_named_ref(eval_block.get("model"), f.model_refs)
            _collect_named_ref(eval_block.get("dataset"), f.dataset_refs)
        if "trajectory" in data and isinstance(data["trajectory"], list):
            f.sample_count = max(f.sample_count, len(data["trajectory"]))

    for _path, key, value in _walk(data):
        lk = key.lower()
        tokens = _key_tokens(key)
        value_text = _short_text(value)

        # Counts use _container_count directly (no forced minimum): an empty list
        # or dict contributes 0, so a phantom `"scores": []` is not over-counted.
        if tokens & {"message", "messages"}:
            f.message_count += _container_count(value)
        if tokens & {"event", "events", "trace", "trajectory", "transcript"}:
            f.event_count += _container_count(value)
        if tokens & {"tool", "tools", "toolcall", "toolcalls"} or value_text in {"tool_use", "toolcall", "tool_call"}:
            f.tool_call_count += _container_count(value)
            _collect_named_ref(value, f.permission_refs)
        if tokens & {"score", "scores", "scorer", "scorers"}:
            f.score_count += _container_count(value)
            _collect_named_ref(value, f.scorer_refs)
        if tokens & {"monitor", "monitors", "monitoring"}:
            f.monitor_count += _container_count(value)
            _collect_named_ref(value, f.monitor_refs)
        if tokens & {"error", "errors", "exception", "exceptions"} or value_text == "error":
            f.error_count += _container_count(value)
        if tokens & {"retry", "retries"}:
            f.retry_count += _container_count(value)
        if tokens & {"timeout", "timeouts"}:
            f.timeout_count += _container_count(value)

        if tokens & {"model", "models", "provider"}:
            _collect_named_ref(value, f.model_refs)
        if tokens & {"prompt", "prompts", "system", "input", "instructions", "instruction"}:
            _collect_named_ref(value, f.prompt_refs)
        if tokens & {"policy", "policies", "bundle", "bundles", "scaffold", "scaffolds"}:
            _collect_named_ref(value, f.policy_refs)
        if tokens & {"permission", "permissions", "sandbox", "network", "allow", "allowed"}:
            _collect_named_ref(value, f.permission_refs)
        if lk in {"decision_id", "decisionid"} or {"decision", "id"} <= tokens:
            _collect_named_ref(value, f.decision_ids)
        if (
            "uuid" in tokens
            or lk in {"trace_id", "traceid", "sessionid", "session_id", "id"}
            or {"trace", "id"} <= tokens
            or {"session", "id"} <= tokens
        ):
            if "decision" not in str(value).lower():
                _collect_named_ref(value, f.trace_ids)
        if tokens & {"replay", "replays"}:
            _collect_named_ref(value, f.replay_refs)
        if tokens & {"counterfactual", "counterfactuals"}:
            _collect_named_ref(value, f.counterfactual_refs)
        if (
            tokens & {"leakage", "contamination", "scrub", "scrubbed"}
            or any(t.startswith("anonym") for t in tokens)
            or {"reward", "hacking"} <= tokens
        ):
            _collect_named_ref(value, f.leakage_refs)
        if lk in {"task", "task_id", "benchmark", "case_id", "category"} or {"task", "id"} <= tokens:
            _collect_named_ref(value, f.task_refs)
        if lk in {"dataset", "split", "sample_id"} or {"sample", "id"} <= tokens:
            _collect_named_ref(value, f.dataset_refs)

    for ref in list(f.model_refs):
        if RESOLVED_MODEL_RE.search(ref):
            f.resolved_model_refs.add(ref)

    f.message_count = min(f.message_count, _max_reasonable_count(data, ("messages", "message")))
    f.event_count = min(f.event_count, _max_reasonable_count(data, ("events", "event", "trace", "trajectory")))
    if f.sample_count == 0 and f.record_count > 1:
        f.sample_count = f.record_count
    return f


def _walk(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], str, Any]]:
    """Yield ``(path, key, value)`` for every dict key in a JSON-like structure.

    Iterative (explicit stack) rather than recursive: a pathologically deep or
    adversarial trace must not raise ``RecursionError``, which would otherwise
    escape as a traceback and be indistinguishable from a gate block. Every dict
    key is visited exactly once; downstream consumers accumulate into order-free
    sets and counters, so the depth-first (siblings-before-descendants) visit
    order is equivalent to the previous recursion.
    """
    stack: list[tuple[Any, tuple[str, ...]]] = [(value, path)]
    while stack:
        node, node_path = stack.pop()
        if len(node_path) > MAX_TRACE_DEPTH:
            raise TraceTooDeepError(f"trace nesting exceeds MAX_TRACE_DEPTH ({MAX_TRACE_DEPTH})")
        if isinstance(node, dict):
            for k, v in node.items():
                key = str(k)
                child_path = node_path + (key,)
                yield child_path, key, v
                stack.append((v, child_path))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                stack.append((item, node_path + (str(i),)))


def _container_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return 1
    if value is None:
        return 0
    return 1


def _max_reasonable_count(data: Any, names: tuple[str, ...]) -> int:
    total = 0
    for _, key, value in _walk(data):
        if key.lower() in names:
            total += _container_count(value)
    return max(total, 1)


# Named-ref collection only descends into a fixed set of identifier keys and the
# first few list items, so realistic traces nest a handful of levels at most. The
# depth cap defends against an adversarial deep chain (e.g. dicts nested thousands
# deep under an "id" key) so it degrades gracefully instead of raising
# RecursionError; the CLI additionally maps any RecursionError to a usage error.
_MAX_REF_DEPTH = 64


def _collect_named_ref(value: Any, target: set[str], _depth: int = 0) -> None:
    if value is None or _depth > _MAX_REF_DEPTH:
        return
    if isinstance(value, (str, int, float, bool)):
        text = str(value).strip()
        if text:
            if len(text) > 80:
                target.add(f"text_sha256:{sha256(text.encode('utf-8')).hexdigest()[:16]} len:{len(text)}")
            else:
                target.add(text)
        return
    if isinstance(value, dict):
        for key in (
            "id",
            "name",
            "model",
            "model_id",
            "model_name",
            "version",
            "digest",
            "hash",
            "policy_bundle_version",
            "scorer",
            "monitor",
            "tool",
            "resource",
            "status",
            "event",
            "type",
        ):
            if key in value:
                _collect_named_ref(value[key], target, _depth + 1)
        return
    if isinstance(value, list):
        for item in value[:5]:
            _collect_named_ref(item, target, _depth + 1)


def _short_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip().lower()[:80]
    return ""


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
