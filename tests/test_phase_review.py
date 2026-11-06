"""Tests for fail-closed review against actual SAMOCODE phase rules."""

import importlib
from pathlib import Path
import unittest
from unittest.mock import patch

import worker.phase_review as review

GOOD = "............\n----------------------------------------------------------------------\nRan 12 tests in 0.001s\n\nOK\n"


class ReviewPhaseTransitionTests(unittest.TestCase):
    def call(self, source="implementation", target="testing", stderr=GOOD, exit_code=0):
        return review.review_phase_transition(source, target, stderr, exit_code)

    def test_implementation_to_testing(self):
        self.assertEqual(self.call(), {
            "status": "TRANSITION_REVIEWED_NOT_COMMITTED",
            "source": "implementation", "target": "testing", "tests_passed": 12,
        })

    def test_testing_to_quality(self):
        self.assertEqual(self.call("testing", "quality")["tests_passed"], 12)

    def test_quality_to_testing(self):
        self.assertEqual(self.call("quality", "testing")["target"], "testing")

    def test_quality_to_done_rejected(self):
        with self.assertRaises(ValueError):
            self.call("quality", "done")

    def test_implementation_to_quality_rejected(self):
        with self.assertRaises(ValueError):
            self.call("implementation", "quality")

    def test_testing_to_pr_readiness_rejected_without_proof(self):
        with self.assertRaisesRegex(ValueError, "proof"):
            self.call("testing", "pr-readiness")

    def test_pr_readiness_to_done_rejected_without_proof(self):
        with self.assertRaises(ValueError):
            self.call("pr-readiness", "done")

    def test_source_type_invalid(self):
        for value in (None, 1, True, b"testing"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                self.call(value, "testing")

    def test_target_type_invalid(self):
        for value in (None, 1, True, b"testing"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                self.call("implementation", value)

    def test_str_subclass_invalid(self):
        class StrSubclass(str):
            pass
        with self.assertRaises(TypeError):
            self.call(StrSubclass("implementation"), "testing")
        with self.assertRaises(TypeError):
            self.call("implementation", StrSubclass("testing"))

    def test_failed_exit_rejected(self):
        with self.assertRaises(ValueError):
            self.call(exit_code=1)

    def test_failed_qa_report_rejected(self):
        with self.assertRaises(ValueError):
            self.call(stderr="Ran 12 tests in 0.001s\nFAILED (failures=1)\n")

    def test_too_few_tests_rejected(self):
        with self.assertRaises(ValueError):
            self.call(stderr="Ran 9 tests in 0.001s\n\nOK\n")

    def test_bool_exit_code_rejected(self):
        with self.assertRaises(TypeError):
            self.call(exit_code=False)

    def test_qa_parser_is_actual_a_import_and_called(self):
        original = importlib.import_module("worker.qa_result_parser").parse_unittest_success
        self.assertIs(review.parse_unittest_success, original)
        with patch.object(review, "parse_unittest_success", wraps=original) as parsed:
            self.call()
            parsed.assert_called_once_with(GOOD, 0)

    def test_upstream_phase_functions_are_original_imports_and_called(self):
        upstream = importlib.import_module("worker.phases")
        self.assertIs(review.validate_transition, upstream.validate_transition)
        self.assertIs(review.validate_signal_for_phase, upstream.validate_signal_for_phase)
        with patch.object(review, "validate_transition", wraps=upstream.validate_transition) as vt:
            with patch.object(review, "validate_signal_for_phase", wraps=upstream.validate_signal_for_phase) as vs:
                self.call()
                vt.assert_called_once_with("implementation", "testing")
                vs.assert_called_once_with("implementation", "continue")

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            self.call("unknown", "testing")

    def test_source_denies_continue_signal(self):
        with self.assertRaises(ValueError):
            self.call("done", "done")

    def test_no_mutation_or_files_written(self):
        source = "implementation"
        target = "testing"
        stderr = GOOD
        with patch("builtins.open", side_effect=AssertionError("unexpected file access")):
            with patch.object(Path, "write_text", side_effect=AssertionError("unexpected write")):
                result = self.call(source, target, stderr)
        self.assertEqual((source, target, stderr), ("implementation", "testing", GOOD))
        self.assertEqual(result["status"], "TRANSITION_REVIEWED_NOT_COMMITTED")

    def test_exact_ten_tests_accepted(self):
        report = "Ran 10 tests in 0.025s\n\nOK\n"
        self.assertEqual(self.call(stderr=report)["tests_passed"], 10)


if __name__ == "__main__":
    unittest.main()
