"""Extractor hardening tests: token matching, no empty-container over-count, bounded depth."""

from __future__ import annotations

import unittest

from inspect_evidence_sufficiency.extract import (
    MAX_TRACE_DEPTH,
    TraceTooDeepError,
    _key_tokens,
    extract_features,
)


class KeyTokenTests(unittest.TestCase):
    def test_tokenises_snake_and_camel(self) -> None:
        self.assertEqual(_key_tokens("tool_call"), {"tool", "call"})
        self.assertEqual(_key_tokens("toolCall"), {"tool", "call"})
        self.assertEqual(_key_tokens("data_model"), {"data", "model"})
        self.assertEqual(_key_tokens("toolbar"), {"toolbar"})  # not {"tool", ...}


class ExtractorResistsCosmeticKeysTests(unittest.TestCase):
    def test_lookalike_keys_do_not_inflate_counts(self) -> None:
        # None of these are real tool/model/score evidence; they must not register.
        features = extract_features(
            {
                "toolbar": ["x", "y"],
                "remodel": "nope",
                "scoreboard": 3,
                "retrying_again": True,
                "timeoutish": "no",
            }
        )
        self.assertEqual(features.tool_call_count, 0)
        self.assertEqual(features.score_count, 0)
        self.assertEqual(features.model_refs, set())
        self.assertEqual(features.retry_count, 0)
        self.assertEqual(features.timeout_count, 0)

    def test_real_keys_still_register(self) -> None:
        features = extract_features(
            {"tool_call": {"tool": "read_file"}, "data_model": "gpt-4o-2024-08-06", "scores": {"acc": 1}}
        )
        self.assertGreaterEqual(features.tool_call_count, 1)
        self.assertIn("gpt-4o-2024-08-06", features.model_refs)
        self.assertGreaterEqual(features.score_count, 1)

    def test_empty_containers_count_zero(self) -> None:
        # The empty-container over-count bug: "scores": [] must contribute 0, not 1.
        features = extract_features({"scores": [], "tools": [], "errors": []})
        self.assertEqual(features.score_count, 0)
        self.assertEqual(features.tool_call_count, 0)
        self.assertEqual(features.error_count, 0)


class BoundedDepthTests(unittest.TestCase):
    def test_deeply_nested_input_raises_bounded_error_not_recursion(self) -> None:
        # The iterative walk must raise a bounded TraceTooDeepError (a ValueError the
        # CLI maps to exit 2), never a RecursionError traceback.
        node: dict = {}
        cur = node
        for _ in range(MAX_TRACE_DEPTH + 50):
            cur["a"] = {}
            cur = cur["a"]
        with self.assertRaises(TraceTooDeepError):
            extract_features(node)

    def test_moderately_nested_input_is_fine(self) -> None:
        # Well within the cap: a normal-depth trace extracts without raising.
        node: dict = {}
        cur = node
        for _ in range(50):
            cur["events"] = [{}]
            cur = cur["events"][0]
        cur["model"] = "gpt-4o-2024-08-06"
        features = extract_features(node)
        self.assertIn("gpt-4o-2024-08-06", features.model_refs)


if __name__ == "__main__":
    unittest.main()
