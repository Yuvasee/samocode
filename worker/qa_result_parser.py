"""Validate a successful standard-library unittest stderr report."""

import re


_SUMMARY = re.compile(r"Ran ([1-9][0-9]*) tests? in [0-9]+(?:\.[0-9]+)?s")


def parse_unittest_success(stderr, exit_code):
    """Return the positive test count only for a clean unittest run."""
    if type(stderr) is not str or type(exit_code) is not int:
        raise TypeError("stderr must be str and exit_code must be a built-in int")
    if exit_code != 0:
        raise ValueError("unittest exited unsuccessfully")
    if "FAILED" in stderr or "Traceback" in stderr:
        raise ValueError("unittest reported a failure or traceback")

    lines = stderr.splitlines()
    nonempty = [line.strip() for line in lines if line.strip()]
    if not nonempty or nonempty[-1] != "OK":
        raise ValueError("final non-empty line must be exactly OK")

    matches = [_SUMMARY.fullmatch(line.strip()) for line in lines]
    counts = [int(match.group(1)) for match in matches if match is not None]
    if len(counts) != 1:
        raise ValueError("expected exactly one valid unittest run summary")
    return counts[0]
