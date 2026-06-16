# Review Debt

Open blocking/important findings from quality review. No row may remain `undecided`
when quality completes.

Quality Iteration: 1 (initial review)
Last updated: 06-06 15:20

## Status Legend
- Decision: `fix now` | `defer` | `reject`
- Status: `open` | `resolved` | `deferred`

| ID | Severity | Finding | Decision | Status | Evidence |
|---|---|---|---|---|---|
| Q-001 | important | `_is_samocode_owned` string-prefix false positive (sibling tree treated as owned) | fix now | resolved | path-aware containment; `test_owned_rejects_sibling_prefix_dir` |
| Q-002 | important | `install_asset` clobbered foreign colliding symlinks | fix now | resolved | ownership check before unlink; `test_install_asset_skips_foreign_symlink` |
| Q-003 | important | Missing `tests/test_installer.py` (one-file-per-module convention) | fix now | resolved | 32 tests added; 234 suite green |
| Q-004 | important | README idempotency/uninstall claims contradict copy-mode behavior | fix now | resolved | README install section rewritten |
| Q-005 | important | No `OSError` handling in install/uninstall (traceback on failure) | fix now | resolved | per-asset try/except in install()/uninstall() |
| Q-006 | important | Orphaned dangling symlinks from removed `adhd`/`gemini` skills (old-install.sh users) | fix now | resolved | README "Upgrading from old install.sh" migration note |
| Q-007 | suggestion | `agents/AGENT_TEMPLATE.md` installed as broken agent | fix now | resolved | `*_TEMPLATE.md` excluded; `test_enumerate_file_asset_excludes_template` |

No undecided rows. All findings resolved this iteration.

## Reviewer notes
- Gemini (performance/testing) review **skipped** — YOLO mode disabled by administrator
  on this host. Testing-gap coverage was instead supplied by the System Architect and
  Product reviewers (both flagged the missing installer tests, now resolved).
