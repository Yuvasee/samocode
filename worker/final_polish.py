"""Deterministic proof that autonomous final polish covered the current HEAD."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .lifecycle import TESTING_RUN_SECOND, validate_final_polish_lifecycle

_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_DEBT_ID = re.compile(r"^(?:CL|Q)-\d+$", re.IGNORECASE)

# Closed vocabularies. Membership tests and the "(got ...)" messages both read
# from these tuples, so accepted tokens live in exactly one place.
_CLARITY_RESULTS: tuple[str, ...] = ("clean", "findings")
_DEBT_DECISIONS: tuple[str, ...] = ("fix now", "defer", "reject")
# A `fix now` row must carry one of these explicit closed statuses; any other value
# (including empty, `open`, `in progress`, `not fixed`) means the finding is unresolved.
_DEBT_FIXED_STATUSES: tuple[str, ...] = ("fixed", "closed", "resolved", "verified")
_EMPHASIS_CHARS = "*_`"


@dataclass(frozen=True)
class FinalPolishCheck:
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_final_polish(session_path: Path, working_dir: Path) -> FinalPolishCheck:
    evidence = validate_final_polish_evidence(session_path, working_dir)
    lifecycle = validate_final_polish_lifecycle(session_path)
    return FinalPolishCheck((*evidence.errors, *lifecycle.errors))


def validate_final_polish_evidence(
    session_path: Path, working_dir: Path
) -> FinalPolishCheck:
    """Validate final artifacts and repository state without lifecycle history."""
    errors: list[str] = []
    clarity = _latest_metadata(
        session_path,
        "*-code-clarity*.md",
        ("Reviewed HEAD", "Result", "Disposition"),
        "Code Clarity",
        errors,
    )
    hygiene = _latest_metadata(
        session_path,
        "*-comment-hygiene.md",
        ("Input HEAD", "Output HEAD", "Safety check"),
        "Comment Hygiene",
        errors,
    )
    regression = _latest_metadata(
        session_path,
        "*-test-report.md",
        ("Run", "Result", "Tested HEAD"),
        "post-quality regression",
        errors,
    )

    current_head = _git_output(working_dir, ("rev-parse", "HEAD"), errors)
    status = _git_output(
        working_dir,
        ("status", "--porcelain", "--untracked-files=all"),
        errors,
    )
    if status:
        errors.append("Project working tree is not clean after Comment Hygiene")

    reviewed = _sha(clarity, "Reviewed HEAD", "Code Clarity", errors)
    hygiene_input = _sha(hygiene, "Input HEAD", "Comment Hygiene", errors)
    hygiene_output = _sha(hygiene, "Output HEAD", "Comment Hygiene", errors)
    tested = _sha(regression, "Tested HEAD", "regression", errors)

    if clarity:
        result_raw = clarity["Result"]
        if _normalize_token(result_raw) not in _CLARITY_RESULTS:
            errors.append(
                _mismatch(
                    f"Result must be exactly {_quoted(_CLARITY_RESULTS)}", result_raw
                )
            )
        disposition_raw = clarity["Disposition"]
        if _normalize_token(disposition_raw) != "settled":
            errors.append(_mismatch("Disposition must be `settled`", disposition_raw))
    if hygiene:
        safety_raw = hygiene["Safety check"]
        if _normalize_token(safety_raw) != "pass":
            errors.append(_mismatch("Safety check must be `pass`", safety_raw))
    if regression:
        run_raw = regression["Run"]
        if _normalize_token(run_raw) != TESTING_RUN_SECOND:
            errors.append(_mismatch(f"Run must be `{TESTING_RUN_SECOND}`", run_raw))
        regression_result_raw = regression["Result"]
        if _normalize_token(regression_result_raw) != "pass":
            errors.append(
                _mismatch(
                    "post-quality regression Result must be `pass`",
                    regression_result_raw,
                )
            )

    _require_equal(
        reviewed, hygiene_input, "Code Clarity HEAD != Hygiene input", errors
    )
    _require_equal(hygiene_output, tested, "Hygiene output != regression HEAD", errors)
    _require_equal(
        hygiene_output, current_head, "Hygiene output != current HEAD", errors
    )
    _validate_review_debt(session_path, errors)
    return FinalPolishCheck(tuple(errors))


def _latest_metadata(
    session_path: Path,
    pattern: str,
    fields: tuple[str, ...],
    label: str,
    errors: list[str],
) -> dict[str, str] | None:
    matches = sorted(session_path.glob(pattern))
    if not matches:
        errors.append(f"Missing {label} artifact ({pattern})")
        return None
    path = matches[-1]
    try:
        content = path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"Cannot read latest {label} artifact {path.name}: {exc}")
        return None

    metadata: dict[str, str] = {}
    for field in fields:
        values = re.findall(
            rf"^{re.escape(field)}:[ \t]*(.*)$", content, flags=re.MULTILINE
        )
        if len(values) != 1 or not values[0].strip():
            errors.append(
                f"Latest {label} artifact {path.name} must contain exactly one "
                f"non-empty '{field}:' field"
            )
            continue
        metadata[field] = values[0].strip()
    return metadata if len(metadata) == len(fields) else None


def _sha(
    metadata: dict[str, str] | None,
    field: str,
    label: str,
    errors: list[str],
) -> str | None:
    if metadata is None:
        return None
    value = metadata[field]
    if not _FULL_SHA.fullmatch(value):
        errors.append(f"{label} field '{field}' must be a full 40-character git SHA")
        return None
    return value.lower()


def _git_output(
    working_dir: Path, args: tuple[str, ...], errors: list[str]
) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(working_dir), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        errors.append(f"Cannot execute git for final-polish validation: {exc}")
        return None
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        errors.append(f"git {' '.join(args)} failed: {detail}")
        return None
    return result.stdout.strip()


def _require_equal(
    left: str | None, right: str | None, message: str, errors: list[str]
) -> None:
    if left is not None and right is not None and left != right:
        errors.append(message)


def _validate_review_debt(session_path: Path, errors: list[str]) -> None:
    path = session_path / "_review_debt.md"
    if not path.exists():
        return
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"Cannot read review debt ledger: {exc}")
        return
    columns: dict[str, int] | None = None
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        normalized_cells = [_normalize_token(cell) for cell in cells]
        if "id" in normalized_cells and "decision" in normalized_cells:
            columns = {
                name: index
                for index, name in enumerate(normalized_cells)
                if name in {"id", "decision", "status"} or _is_evidence_header(name)
            }
            continue
        if not cells or not _DEBT_ID.fullmatch(cells[0]):
            continue
        if columns is None:
            if "undecided" in normalized_cells or "open" in normalized_cells:
                errors.append(f"Review debt row {cells[0]} is still open or undecided")
            continue

        decision_raw = _debt_cell(cells, columns.get("decision"))
        decision = _normalize_token(decision_raw)
        status_raw = _debt_cell(cells, columns.get("status"))
        status = _normalize_token(status_raw)
        evidence_index = next(
            (index for name, index in columns.items() if _is_evidence_header(name)),
            None,
        )
        evidence = _debt_cell(cells, evidence_index)
        if decision in {"", "undecided"}:
            errors.append(f"Review debt row {cells[0]} has no explicit decision")
        elif decision == "fix now" and status not in _DEBT_FIXED_STATUSES:
            errors.append(
                _mismatch(
                    f"Review debt row {cells[0]} selected fix now but its status is "
                    f"not one of {_quoted(_DEBT_FIXED_STATUSES)}",
                    status_raw,
                )
            )
        elif decision in {"defer", "reject"} and not evidence:
            if evidence_index is None:
                errors.append(
                    "ledger needs a column whose header contains `Evidence` or `Ticket`"
                )
            else:
                errors.append(
                    f"Review debt row {cells[0]} decision `{decision}` lacks evidence"
                )
        elif decision not in _DEBT_DECISIONS:
            errors.append(
                _mismatch(
                    f"Review debt row {cells[0]} decision must be one of "
                    f"{_quoted(_DEBT_DECISIONS)}",
                    decision_raw,
                )
            )


def _normalize_token(value: str) -> str:
    # Strips surrounding emphasis/backticks/whitespace only, never parenthetical
    # suffixes, so `reject (not promoted)` stays outside the closed vocabulary.
    return value.strip().strip(_EMPHASIS_CHARS).strip().casefold()


def _quoted(tokens: tuple[str, ...]) -> str:
    quoted = [f"`{token}`" for token in tokens]
    if len(quoted) < 2:
        return "".join(quoted)
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def _mismatch(expectation: str, raw: str) -> str:
    return f"{expectation} (got `{raw}`)"


def _is_evidence_header(header: str) -> bool:
    return "evidence" in header or "ticket" in header


def _debt_cell(cells: list[str], index: int | None) -> str:
    if index is None or index >= len(cells):
        return ""
    return cells[index].strip()
