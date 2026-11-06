"""Fail-closed, non-committing transition review for saved unittest evidence."""

from .qa_result_parser import parse_unittest_success
from .phases import validate_transition, validate_signal_for_phase


def review_phase_transition(source, target, stderr, exit_code):
    """Review only; never commit a phase or write SAMOCODE signal files."""
    count = parse_unittest_success(stderr, exit_code)
    if count < 10:
        raise ValueError("At least ten passing unittest tests are required")
    if type(source) is not str or type(target) is not str:
        raise TypeError("source and target must be built-in str")
    signal_ok, signal_reason = validate_signal_for_phase(source, "continue")
    if not signal_ok:
        raise ValueError(signal_reason)
    transition_ok, transition_reason = validate_transition(source, target)
    if not transition_ok:
        raise ValueError(transition_reason)
    if target.lower() in ("pr-readiness", "done"):
        raise ValueError("A reviewed repository and PR proof is required")
    return {
        "status": "TRANSITION_REVIEWED_NOT_COMMITTED",
        "source": source,
        "target": target,
        "tests_passed": count,
    }
