"""Implementation-plan profile resolution.

Parses `_overview.md`'s `## Plans` section to find the active plan file,
parses that plan's `## Implementation Phases` section into phases, and
selects the first phase with an unchecked task.

Pure parsing (Markdown text -> dataclasses) is kept separate from resolution
(filesystem reads + selection) so the parser is testable with plain strings.
This module knows nothing about `GlobalConfig` or `Provider`; Phase 5's
execution-target resolver in `worker/routing.py` validates an explicit plan
profile name against the selected provider's profile table.
"""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# === Errors ===


class PlanResolutionError(ValueError):
    """Raised when `_overview.md` or the active plan file is missing, stale,
    or structurally invalid. Always raised before any model call."""


# === Data structures ===


class PlanProfileSource(Enum):
    """Where a resolved implementation-plan profile came from."""

    PLAN_PHASE_EXPLICIT = "plan_phase_explicit"  # `**Profile:**` on active phase
    IMPLEMENTATION_DEFAULT = "implementation_default"  # omitted, or all complete


@dataclass(frozen=True)
class PlanEntry:
    """One bullet in `_overview.md`'s `## Plans` section."""

    filename: str
    description: str


@dataclass(frozen=True)
class PlanPhase:
    """One `### Phase <label>: <title>` block under `## Implementation Phases`."""

    label: str  # raw heading identifier, e.g. "1", "3a", "10"
    title: str
    profile: str | None
    total_tasks: int
    completed_tasks: int

    @property
    def has_unchecked_task(self) -> bool:
        return self.completed_tasks < self.total_tasks


@dataclass(frozen=True)
class PlanPhaseSelection:
    """Resolved active implementation-plan phase.

    Consumed by Phase 5's execution target and Phase 8's runner context.
    `profile` is None when the phase omits `**Profile:**` (or all phases are
    complete); the caller then applies the workflow `implementation` default.
    """

    plan_path: Path
    phase_label: str | None  # None only when all_complete
    phase_title: str | None
    profile: str | None
    source: PlanProfileSource
    all_complete: bool


# === Parsing: `_overview.md` -> active plan path ===

_PLANS_HEADING_RE = re.compile(r"^## Plans\s*$")
_LEVEL2_HEADING_RE = re.compile(r"^## ")
_PLAN_ENTRY_RE = re.compile(r"^- (\S+\.md)(?:\s*-\s*(.*))?$")


def parse_plan_entries(overview_text: str) -> list[PlanEntry]:
    """Parse `_overview.md`'s `## Plans` section into ordered entries.

    Stops at the next level-2 heading or end of file. Non-matching lines in the
    section (blank lines, prose) are skipped, not treated as errors.
    """
    entries: list[PlanEntry] = []
    in_section = False
    for line in overview_text.splitlines():
        if _PLANS_HEADING_RE.match(line):
            in_section = True
            continue
        if in_section and _LEVEL2_HEADING_RE.match(line):
            break
        if not in_section:
            continue
        match = _PLAN_ENTRY_RE.match(line.strip())
        if match:
            entries.append(
                PlanEntry(
                    filename=match.group(1),
                    description=(match.group(2) or "").strip(),
                )
            )
    return entries


def select_active_plan(session_dir: Path, overview_text: str) -> Path:
    """Resolve the active plan file from `_overview.md`'s `## Plans` section.

    Selection rule: the LAST entry. The planning skill/agent only append to
    `## Plans`; the most recently appended plan drives implementation.
    Filesystem glob order and mtime are not used.

    Raises PlanResolutionError if `## Plans` is missing/empty, or the selected
    entry names a file that does not exist under `session_dir` (stale reference).
    """
    entries = parse_plan_entries(overview_text)
    if not entries:
        raise PlanResolutionError(
            "_overview.md has no `## Plans` entries; a plan must be created "
            "before implementation can resolve a phase"
        )
    active = entries[-1]
    plan_path = session_dir / active.filename
    if not plan_path.is_file():
        raise PlanResolutionError(
            f"_overview.md `## Plans` references {active.filename!r}, but "
            f"{plan_path} does not exist (stale plan reference)"
        )
    return plan_path


# === Parsing: plan file -> phases ===

_IMPL_PHASES_HEADING_RE = re.compile(r"^## Implementation Phases\s*$")
_PHASE_HEADING_RE = re.compile(r"^### Phase ([0-9A-Za-z]+):\s*(.+?)\s*$")
_PROFILE_LINE_RE = re.compile(r"^\*\*Profile:\*\*\s*(.*)$")
_VALID_PROFILE_VALUE_RE = re.compile(r"^`([^`\s][^`]*)`$")
_TASK_LINE_RE = re.compile(r"^-\s\[([ xX])\]")


def parse_implementation_phases(plan_text: str) -> list[PlanPhase]:
    """Parse only the `## Implementation Phases` section of a plan file.

    Stops at the next level-2 heading or end of file; headings/checkboxes/
    Profile-shaped lines elsewhere (Requirements, Notes) are never considered.
    A phase with no task lines is retained but non-selectable.

    Raises PlanResolutionError for a missing section, a malformed `**Profile:**`
    value, or a second/misplaced `**Profile:**` line within one phase.
    """
    lines = plan_text.splitlines()
    section_start = _find_section_start(lines)
    if section_start is None:
        raise PlanResolutionError("plan file has no `## Implementation Phases` section")

    section_lines = _slice_until_next_level2(lines, section_start)
    return [
        _parse_one_phase(label, title, body)
        for label, title, body in _split_into_phase_blocks(section_lines)
    ]


def _find_section_start(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if _IMPL_PHASES_HEADING_RE.match(line):
            return i + 1
    return None


def _slice_until_next_level2(lines: list[str], start: int) -> list[str]:
    out: list[str] = []
    for line in lines[start:]:
        if _LEVEL2_HEADING_RE.match(line):
            break
        out.append(line)
    return out


def _split_into_phase_blocks(
    section_lines: list[str],
) -> list[tuple[str, str, list[str]]]:
    """Group section lines into (label, title, body) per `### Phase` heading.
    Content before the first heading is discarded."""
    blocks: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] | None = None
    for line in section_lines:
        match = _PHASE_HEADING_RE.match(line)
        if match:
            if current is not None:
                blocks.append(current)
            current = (match.group(1), match.group(2), [])
        elif current is not None:
            current[2].append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def _parse_one_phase(label: str, title: str, body: list[str]) -> PlanPhase:
    profile = _extract_profile(label, title, body)
    total = completed = 0
    for line in body:
        task_match = _TASK_LINE_RE.match(line.strip())
        if task_match:
            total += 1
            if task_match.group(1).lower() == "x":
                completed += 1
    return PlanPhase(
        label=label,
        title=title,
        profile=profile,
        total_tasks=total,
        completed_tasks=completed,
    )


def _extract_profile(label: str, title: str, body: list[str]) -> str | None:
    """Return the phase's explicit profile name, or None if omitted.

    The `**Profile:**` line must be the first non-blank line after the heading.
    Any other Profile-shaped line in the body is a duplicate/misplaced error.
    """
    non_blank = [line.strip() for line in body if line.strip()]

    first_profile_value: str | None = None
    if non_blank:
        match = _PROFILE_LINE_RE.match(non_blank[0])
        if match:
            first_profile_value = _validate_profile_value(label, title, match.group(1))

    rest = non_blank[1:] if first_profile_value is not None else non_blank
    for line in rest:
        if _PROFILE_LINE_RE.match(line):
            raise PlanResolutionError(
                f"Phase {label} ({title!r}): `**Profile:**` must be the first "
                "line after the heading; found a second or misplaced declaration"
            )
    return first_profile_value


def _validate_profile_value(label: str, title: str, raw_value: str) -> str:
    match = _VALID_PROFILE_VALUE_RE.match(raw_value.strip())
    if not match:
        raise PlanResolutionError(
            f"Phase {label} ({title!r}): malformed `**Profile:**` value "
            f"{raw_value!r}; expected one non-empty backtick-quoted name, "
            "e.g. **Profile:** `strong`"
        )
    return match.group(1)


# === Resolution ===


def select_active_phase(phases: list[PlanPhase]) -> PlanPhase | None:
    """First phase, in document order, with an unchecked task; None if all
    complete."""
    for phase in phases:
        if phase.has_unchecked_task:
            return phase
    return None


def resolve_plan_phase(session_dir: Path) -> PlanPhaseSelection:
    """Resolve the active plan phase and its profile for one implementation
    iteration.

    Reads `session_dir/_overview.md`, selects the active plan (last `## Plans`
    entry), parses its `## Implementation Phases`, and selects the first phase
    with an unchecked task.

    Raises PlanResolutionError for any missing/stale/malformed input, always
    before returning; callers must not catch it to substitute a default profile.
    """
    overview_path = session_dir / "_overview.md"
    if not overview_path.is_file():
        raise PlanResolutionError(f"session overview not found: {overview_path}")

    plan_path = select_active_plan(session_dir, overview_path.read_text())
    phases = parse_implementation_phases(plan_path.read_text())
    active = select_active_phase(phases)

    if active is None:
        return PlanPhaseSelection(
            plan_path=plan_path,
            phase_label=None,
            phase_title=None,
            profile=None,
            source=PlanProfileSource.IMPLEMENTATION_DEFAULT,
            all_complete=True,
        )

    source = (
        PlanProfileSource.PLAN_PHASE_EXPLICIT
        if active.profile is not None
        else PlanProfileSource.IMPLEMENTATION_DEFAULT
    )
    return PlanPhaseSelection(
        plan_path=plan_path,
        phase_label=active.label,
        phase_title=active.title,
        profile=active.profile,
        source=source,
        all_complete=False,
    )
