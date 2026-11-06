"""Saved unittest coverage for the strict unittest stderr parser."""

import unittest

from worker.qa_result_parser import parse_unittest_success


def report(n, prefix=".", duration="0.001", ending="OK"):
    noun = "test" if n == 1 else "tests"
    return f"{prefix}\n----------------------------------------------------------------------\nRan {n} {noun} in {duration}s\n\n{ending}\n"


class IntSubclass(int):
    pass


class TestParseUnittestSuccess(unittest.TestCase):
    def test_one_test(self):
        self.assertEqual(parse_unittest_success(report(1), 0), 1)

    def test_many_tests(self):
        self.assertEqual(parse_unittest_success(report(123), 0), 123)

    def test_zero_tests_invalid(self):
        with self.assertRaises(ValueError):
            parse_unittest_success(report(0), 0)

    def test_nonzero_exit(self):
        with self.assertRaises(ValueError):
            parse_unittest_success(report(3), 1)

    def test_negative_exit(self):
        with self.assertRaises(ValueError):
            parse_unittest_success(report(3), -1)

    def test_bool_exit_invalid(self):
        with self.assertRaises(TypeError):
            parse_unittest_success(report(3), False)

    def test_int_subclass_exit_invalid(self):
        with self.assertRaises(TypeError):
            parse_unittest_success(report(3), IntSubclass(0))

    def test_stderr_nonstring_invalid(self):
        for value in (None, b"OK", 123):
            with self.subTest(value=value), self.assertRaises(TypeError):
                parse_unittest_success(value, 0)

    def test_missing_output(self):
        with self.assertRaises(ValueError):
            parse_unittest_success("", 0)

    def test_missing_summary(self):
        with self.assertRaises(ValueError):
            parse_unittest_success("...\nOK\n", 0)

    def test_malformed_summary(self):
        for value in ("Ran x tests in 0.1s", "Ran 2 tests in bads", "Ran -2 tests in 0.1s"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_unittest_success(value + "\nOK\n", 0)

    def test_trailing_non_ok(self):
        with self.assertRaises(ValueError):
            parse_unittest_success(report(2) + "extra text\n", 0)

    def test_failed_marker_even_with_ok(self):
        with self.assertRaises(ValueError):
            parse_unittest_success("FAILED\n" + report(2), 0)

    def test_traceback_even_with_ok(self):
        with self.assertRaises(ValueError):
            parse_unittest_success("Traceback (most recent call last):\n" + report(2), 0)

    def test_realistic_verbose_output(self):
        verbose = (
            "test_alpha (__main__.Suite.test_alpha) ... ok\n"
            "test_beta (__main__.Suite.test_beta) ... ok\n"
            "----------------------------------------------------------------------\n"
            "Ran 2 tests in 0.003s\n\nOK\n"
        )
        self.assertEqual(parse_unittest_success(verbose, 0), 2)

    def test_realistic_dots_output(self):
        self.assertEqual(parse_unittest_success(report(5, prefix=".....", duration="1.234"), 0), 5)

    def test_trailing_blank_lines(self):
        self.assertEqual(parse_unittest_success(report(2) + "\n  \n", 0), 2)

    def test_exact_ok_line(self):
        with self.assertRaises(ValueError):
            parse_unittest_success(report(2, ending="OK (skipped=1)"), 0)

    def test_multiple_summaries_rejected(self):
        with self.assertRaises(ValueError):
            parse_unittest_success("Ran 1 test in 0.001s\n" + report(2), 0)

    def test_no_mutation(self):
        stderr = report(9)
        before = stderr[:]
        self.assertEqual(parse_unittest_success(stderr, 0), 9)
        self.assertEqual(stderr, before)


if __name__ == "__main__":
    unittest.main()
