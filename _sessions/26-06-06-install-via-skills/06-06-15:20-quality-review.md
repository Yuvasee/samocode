# Quality Review
Date: 06-06 15:20
Session: install-via-skills
Iteration: 1

## Review Summary

Reviewed the `install-via-skills` branch (replaces bash `install.sh`/`uninstall.sh`
with a Python `samocode install`/`uninstall` subcommand; new `worker/installer.py`,
`main.py` subcommand refactor, skill-catalog edits, README updates).

Ran the `quality` skill: cleanup analysis + multi-perspective review (3 internal
personas via parallel sub-agents + Codex security/correctness via Bash). Gemini
(performance/testing) was **skipped — YOLO mode disabled by administrator** on this
host (`gemini` exits 52).

**No blocking issues.** All 4 reviewers independently returned 0 blocking findings.
The strongest, evidence-backed findings were a path-prefix ownership bug (Codex
*reproduced* it locally), a symlink-clobber gap, the contractual copy-mode uninstall
limitation, and the missing `tests/test_installer.py` (which both an internal
reviewer and the test phase flagged, and which CLAUDE.md mandates). All important
findings were decided **fix now** and implemented this iteration.

## Cleanup Analysis

Scope: `git diff origin/master...HEAD`. `worker/installer.py` is well-typed
(frozen dataclasses, enums, section comments) and matches project style; ruff +
pyright clean. Cleanup concerns surfaced (and overlap with the review below):

- **Dead/unsafe code:** none. No unused exports (all re-exported via `worker/__init__.py`).
- **Type safety:** clean (pyright 0/0/0).
- **Complexity:** `_print_install_summary._count` set-dedup is subtle (suggestion).
- **Documentation drift:** module/`AssetClass` docstrings reference "Phase 2" build
  jargon though that orchestration lives in the same file (suggestion).
- **TODOs/FIXMEs:** none introduced.

## Multi-Perspective Review

Agreement classification across the 4 active reviewers:

| Finding | Reviewers | Agreement | Severity |
|---|---|---|---|
| Ownership check string-prefix false positive (`installer.py:292`) | Codex (reproduced) + Maintainer | Corroborated | important |
| `install_asset` clobbers foreign colliding symlink (`:193`) | Codex | Individual (high) | important |
| Copy-mode uninstall limitation / README contradiction | Codex + Architect + Maintainer + Product | Strong consensus | important |
| Missing `tests/test_installer.py` | Architect + Product (+ test phase + CLAUDE.md) | Corroborated | important |
| No error handling around install/uninstall (OSError → traceback) | Product | Individual (high) | important |
| Orphaned broken symlinks from removed `adhd`/`gemini` skills | Product | Individual (high) | important |
| `AGENT_TEMPLATE.md` installed as broken agent | Product | Individual (high) | suggestion |
| README `--copy` redundant for pip installs | Architect + Product + Maintainer | Strong consensus | suggestion |
| Presentation/print mixed into installer module | Architect | Individual | suggestion |
| `_count` set-dedup subtlety; "Phase 2" docstring jargon; per-asset AUTO re-resolve; `--copy` tri-state mapping; empty-section headers; implicit-`run` arg heuristic | Maintainer/Architect | Individual/Observation | suggestion |

## Blocking Issues

None.

## Important Issues

- [x] Q-001 ownership prefix-match false positive — **fix now** (done)
- [x] Q-002 install clobbers foreign symlink — **fix now** (done)
- [x] Q-003 missing `tests/test_installer.py` — **fix now** (done)
- [x] Q-004 copy-mode uninstall / README contradiction — **fix now (docs)** (done)
- [x] Q-005 no OSError handling in install/uninstall — **fix now** (done)
- [x] Q-006 orphaned symlinks from removed skills — **fix now (README migration note)** (done)

## Required Decisions

| ID | Severity | Finding | Recommended fix | Decision | Evidence / Ticket | Status |
|---|---|---|---|---|---|---|
| Q-001 | important | `_is_samocode_owned` used `str.startswith` → sibling-prefix tree (`samocode-backup`) treated as owned; uninstall could remove a foreign symlink | Path-aware containment (`resolved == src_root or src_root in resolved.parents`) | fix now | Codex reproduced locally; test `test_owned_rejects_sibling_prefix_dir` | resolved |
| Q-002 | important | `install_asset` unconditionally unlinked any existing symlink before checking ownership → clobbers user/foreign symlinks sharing an asset name | Check `_is_samocode_owned` before unlink; SKIP foreign symlinks with warning | fix now | Codex evidence `:193`; test `test_install_asset_skips_foreign_symlink` | resolved |
| Q-003 | important | No `tests/test_installer.py` for 468-line safety-critical module; violates one-file-per-module convention | Add unit tests (modes, outcomes, ownership, enumeration, round-trip) | fix now | CLAUDE.md Testing section; plan Phase 7; 32 tests added | resolved |
| Q-004 | important | README claimed "idempotent refresh" + "reverse with uninstall" but copy-mode neither refreshes nor uninstalls | Scope idempotency/uninstall claims to symlink mode; document copy-mode limitation | fix now | README install section rewritten | resolved |
| Q-005 | important | `OSError` (permission/read-only/disk-full) in install/uninstall aborts with raw traceback, leaves partial state | Per-asset try/except OSError, print error, continue | fix now | install()/uninstall() loops guarded | resolved |
| Q-006 | important | Removed skills (`adhd`,`gemini`) leave dangling symlinks for old-`install.sh` users; never cleaned | README upgrade/migration note with manual `rm` (durable sweep deferred as over-engineering) | fix now | README "Upgrading from old install.sh" note | resolved |
| Q-007 | suggestion | `agents/AGENT_TEMPLATE.md` installed as a broken Claude agent (pre-existing in install.sh) | Exclude `*_TEMPLATE.md` from `_enumerate_asset_entries` | fix now | test `test_enumerate_file_asset_excludes_template` | resolved |

## Non-Blocking Suggestions (logged, not actioned)

- Move `_print_*` helpers / setup banner out of `installer.py` into `main.py` so the
  installer is a pure library (Architect). Deferred — current return-value API already
  lets callers aggregate; presentation split is a larger refactor.
- Resolve AUTO mode once against `src_root` instead of per-asset from `src.parent`
  (Maintainer). Harmless redundancy; left as-is.
- Drop "Phase 2" build-jargon from docstrings (Maintainer). Cosmetic.
- Comment the `_count` set-dedup (skills installed to 2 providers) (Maintainer/Codex). Cosmetic.
- Skip section headers for empty asset classes (Architect). Cosmetic, parity-preserving.
- README `--copy` for pip is now reworded; flag remains a checkout-only override.

## Actions Taken (Quality Iteration 1)

1. `_is_samocode_owned` → path-aware containment (Q-001).
2. `install_asset` gained `src_root` param + ownership-checked symlink refresh; foreign
   symlinks SKIPPED, never clobbered (Q-002).
3. `install()`/`uninstall()` wrap each asset op in `try/except OSError`, report + continue (Q-005).
4. `_enumerate_asset_entries` excludes `*_TEMPLATE.md` (Q-007).
5. README install section: idempotency/uninstall scoped to symlink mode; copy-mode
   limitation documented; pip `--copy` reworded; "Upgrading from old install.sh"
   migration note added (Q-004, Q-006).
6. Added `tests/test_installer.py` — 32 tests covering provider/target resolution,
   AUTO mode detection, install_asset outcomes (incl. foreign-symlink skip & sibling-prefix
   ownership), enumeration (incl. template exclusion), uninstall outcomes, and full
   install/uninstall round-trip in symlink and copy modes (Q-003).

Verification: `pytest tests/` → **234 passed** (202 + 32 new); `ruff check` clean;
`ruff format` applied; `pyright` → 0 errors / 0 warnings.

## Final Status

Issues Remaining: none undecided. All blocking: none. All important findings
decided `fix now` and implemented + verified by tests. Fixes were made → proceed to
regression testing.
