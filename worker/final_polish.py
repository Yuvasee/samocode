"""Deterministic proof that autonomous final polish covered the current HEAD."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .signal_history import HistoryRecord, read_history

_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_DEBT_ID = re.compile(r"^(?:CL|Q)-\d+$", re.IGNORECASE)
_REQUIRED_TRANSITIONS = (
    ("implementation", "testing"),
    ("testing", "quality"),
    ("quality", "testing"),
    ("testing", "pr-readiness"),
)


@dataclass(frozen=True)
class FinalPolishCheck:
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_final_polish(session_path: Path, working_dir: Path) -> FinalPolishCheck:
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
        if clarity["Disposition"].lower() != "settled":
            errors.append("Latest Code Clarity report is not settled")
        if clarity["Result"].lower() not in {"clean", "findings"}:
            errors.append("Latest Code Clarity report has an invalid Result")
    if hygiene and hygiene["Safety check"].upper() != "PASS":
        errors.append("Latest Comment Hygiene safety check did not pass")
    if regression:
        if regression["Run"] != "2nd (post-quality)":
            errors.append("Latest test report is not the post-quality regression run")
        if regression["Result"].upper() != "PASS":
            errors.append("Latest post-quality regression did not pass")

    _require_equal(
        reviewed, hygiene_input, "Code Clarity HEAD != Hygiene input", errors
    )
    _require_equal(hygiene_output, tested, "Hygiene output != regression HEAD", errors)
    _require_equal(
        hygiene_output, current_head, "Hygiene output != current HEAD", errors
    )
    _validate_transition_history(read_history(session_path), errors)
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


def _validate_transition_history(
    history: list[HistoryRecord], errors: list[str]
) -> None:
    transitions = [
        (record.source_phase, record.target_phase)
        for record in history
        if record.accepted is True and record.mutated is True
    ]
    required_index = 0
    for transition in transitions:
        if transition == _REQUIRED_TRANSITIONS[required_index]:
            required_index += 1
            if required_index == len(_REQUIRED_TRANSITIONS):
                break
    if required_index != len(_REQUIRED_TRANSITIONS):
        expected = " -> ".join(target for _, target in _REQUIRED_TRANSITIONS)
        errors.append(
            f"Signal history lacks the ordered final-polish lifecycle: {expected}"
        )
    if not transitions or transitions[-1] != _REQUIRED_TRANSITIONS[-1]:
        errors.append("Latest accepted phase transition is not testing -> pr-readiness")


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
        normalized_cells = [cell.lower() for cell in cells]
        if "id" in normalized_cells and "decision" in normalized_cells:
            columns = {
                name: index
                for index, name in enumerate(normalized_cells)
                if name in {"id", "decision", "status"}
                or name.startswith("evidence")
                or name.startswith("ticket")
            }
            continue
        if not cells or not _DEBT_ID.fullmatch(cells[0]):
            continue
        if columns is None:
            if "undecided" in normalized_cells or "open" in normalized_cells:
                errors.append(f"Review debt row {cells[0]} is still open or undecided")
            continue

        decision = _debt_cell(cells, columns.get("decision")).lower()
        status = _debt_cell(cells, columns.get("status")).lower()
        evidence_index = next(
            (
                index
                for name, index in columns.items()
                if name.startswith("evidence") or name.startswith("ticket")
            ),
            None,
        )
        evidence = _debt_cell(cells, evidence_index)
        if decision in {"", "undecided"}:
            errors.append(f"Review debt row {cells[0]} has no explicit decision")
        elif decision == "fix now" and status in {"", "open", "undecided"}:
            errors.append(
                f"Review debt row {cells[0]} selected fix now but is not closed"
            )
        elif decision in {"defer", "reject"} and not evidence:
            errors.append(
                f"Review debt row {cells[0]} decision '{decision}' lacks evidence"
            )
        elif decision not in {"fix now", "defer", "reject"}:
            errors.append(
                f"Review debt row {cells[0]} has invalid decision '{decision}'"
            )


def _debt_cell(cells: list[str], index: int | None) -> str:
    if index is None or index >= len(cells):
        return ""
    return cells[index].strip()
