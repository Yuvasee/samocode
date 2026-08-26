# Model routing: providers, profiles, and phases

Samocode routes every iteration through a **semantic profile** that resolves to a
concrete model (and optional reasoning effort) for the **one provider** chosen for
the process. This lets a plan say "run this phase at `strong`" without naming a
model, and lets you retune every phase globally in one file.

Requires **Python 3.11+** (uses the stdlib `tomllib` TOML parser).

## Core concepts

| Term | What it is | Chosen where |
|------|-----------|--------------|
| **Orchestration provider** | The CLI that runs every iteration: `claude` or `codex`. Fixed for the whole process. | CLI/env/config/legacy precedence (below) |
| **Semantic profile** | A name like `light`/`standard`/`strong`/`max` (or a custom one). Provider-independent. | Workflow phase default, `[workflow_overrides]`, or a plan phase `**Profile:**` line |
| **Concrete model** | The provider-specific model string a profile resolves to (e.g. `claude-opus-4-8`). | `[providers.<name>.profiles.<profile>]` in the global config |
| **Effort** | Optional reasoning-effort string passed to the provider (e.g. `high`, `xhigh`). Authoritative over the provider's inherited default when set. | Same profile table |
| **Workflow phase** | Outer pipeline stage: `init … done`. Each has a default profile. | `worker/phases.py` + `[workflow_overrides]` |
| **Implementation-plan phase** | A `### Phase N` block inside a session plan. New phases declare a semantic profile; legacy phases may omit it. | Plan file `**Profile:** \`name\`` |

**One provider per process.** A profile never names or switches a provider; it only
selects that provider's model/effort. To run the same task under the other provider,
start a new process with a different provider selection.

**Second opinions still cross providers.** Explicit second-opinion tools (e.g. the
`/multi-review` Gemini reviewer, or a cross-provider `do2`) may call another provider.
That is orthogonal to orchestration routing: the direct `claude`/`codex` skills use
the consulted CLI's own configured/default model rather than silently borrowing the
orchestration profile.

**Agents inherit the routed target.** Every packaged phase agent and normal Claude
workflow sub-agent uses `model: inherit` and leaves effort unset, so both values come
from the routed parent session instead of a pinned Haiku/Sonnet/Opus choice. Codex
performs equivalent independent passes inside the already selected Codex iteration.

## Config location

`$XDG_CONFIG_HOME/samocode/config.toml` when `XDG_CONFIG_HOME` is an absolute path,
otherwise `~/.config/samocode/config.toml`. Loaded once per process.

`samocode install` creates it from built-in defaults only when absent — it **never
overwrites** an existing config. A present-but-invalid config is a fatal error on both
`install` and `run` (fail-fast, never silent).

The same install refreshes Samocode-owned symlinks. In copy mode, existing skills and
commands remain protected; copied Claude phase-agent files are backed up as
`<name>.bak` and refreshed because their inheritance metadata is routing-critical.

## Default profile table

`samocode install` writes these defaults (also printed as a table on install):

| Provider | Profile | Model | Effort |
|----------|---------|-------|--------|
| claude | light | `claude-haiku-4-5-20251001` | — |
| claude | standard | `claude-sonnet-4-6` | high |
| claude | strong | `claude-opus-4-8` | high |
| claude | max | `claude-opus-4-8` | xhigh |
| codex | light | `gpt-5.6-luna` | low |
| codex | standard | `gpt-5.6-terra` | medium |
| codex | strong | `gpt-5.6-sol` | medium |
| codex | max | `gpt-5.6-sol` | xhigh |

`max` maps to `xhigh` effort. Literal effort `"max"` remains a legal custom value.

## Config syntax

```toml
version = 1
default_provider = "claude"
default_profile = "standard"

[providers.claude]
executable = "claude"
[providers.claude.profiles.strong]
model = "claude-opus-4-8"
effort = "high"

# Custom profile: any name, required model, optional effort
[providers.claude.profiles.deep]
model = "claude-opus-4-8"
effort = "max"

[providers.codex]
executable = "codex"
[providers.codex.profiles.strong]
model = "gpt-5.6-sol"
effort = "medium"

# Optional: retarget a workflow phase to a different profile
[workflow_overrides]
investigation = "max"
```

Rules:
- `version` must equal `1`; `default_provider`/`default_profile` must reference an
  existing provider/profile.
- Each profile requires a non-empty `model`; `effort` is optional.
- Any provider name is accepted. Unknown keys are ignored (forward-compatible), so a
  future provider section is inert until that provider is selected.
- `[workflow_overrides]` keys must be real workflow phase names, values must resolve to
  a profile available under the selected provider.

## Provider selection precedence

Highest wins, evaluated once at startup and fixed for the process:

1. CLI `--provider`
2. env `SAMOCODE_PROVIDER`
3. config `default_provider`
4. legacy default `claude`

## Workflow phase defaults

Each outer phase declares a default profile (`worker/phases.py`):

| Phase | Default profile |
|-------|-----------------|
| init | light |
| investigation | strong |
| requirements | strong |
| planning | max |
| implementation | standard (legacy fallback when a plan phase omits `Profile`) |
| testing | strong |
| quality | strong |
| pr-readiness | strong |
| done | light |

Resolution order for a workflow iteration: `[workflow_overrides]` → phase default →
global `default_profile`.

## Testing escalation

`testing` is the only phase with automatic profile escalation. When a testing iteration
blocks on the environment — a missing browser binary, an unreachable dev service, an
unusable interpreter — the worker reruns testing once on the next-stronger profile
instead of stopping. All human gates stay intact.

- **Trigger.** Only an accepted `blocked` signal whose `needs` is `environment`
  (`{"status": "blocked", "needs": "environment"}`). `error_resolution` and
  `human_decision` stay terminal; a product or test failure is never an environment
  blocker and does not escalate.
- **Ladder.** The escalated profile is the next rung above the resolved profile in the
  canonical order `light → standard → strong → max`. Testing defaults to `strong`, so it
  escalates to `max`. There is no escalation from `max`, or when the selected provider's
  profile table lacks the next rung. The provider never changes, and the escalated
  iteration's retries replay the same target.
- **Budget.** One attempt per phase entry, derived from signal history. A second
  `environment` block on the escalated attempt ends the run as a normal block; a later
  re-entry into testing (for example after quality) reopens a fresh attempt.
- **Escalated context.** The rerun receives an `## Escalated Testing Attempt` Session
  Context section: base and escalated `profile/model/effort`, attempt N of M, the blocker
  reason, the latest test-report path, and a generic recovery contract (check the
  project's own environment first; only untracked/temporary/user-level config may change,
  nothing is committed; confirm any remaining blocker with reproducible commands; an
  environment failure is never PASS and mandatory browser E2E is never skipped silently).
- **Audit.** One Flow Log line, one per-iteration routing log line carrying
  `source=escalation` and `escalated_from=<profile>`, one `_signal_history.jsonl` row with
  `status=escalation` (plus `escalated_from_profile`/`escalated_to_profile`), and one
  Telegram notification. The escalation row is inert to phase-transition provenance and
  iteration counting.

## Worktree guard

`testing` is `worktree_readonly`: the worker snapshots HEAD plus tracked-file status
before and after every testing iteration. A tracked-file mutation (edit, format, or
commit) is rejected as `Blocked: workflow_error` (reason `worktree_mutated`) with a
diff summary logged; the testing agent may only touch untracked, temporary, or
user-level files. A non-git working directory skips the guard with a notice.

## Plan-phase profiles

Every phase in a newly authored plan must have a backtick-quoted profile line as the
**first line** under its heading. This also applies to testing/readiness phases and to
new phases added to an existing plan:

```markdown
### Phase 5: Provider-neutral execution resolution
**Profile:** `max`
```

Planning assigns the canonical profiles only from the work and risk of the phase:

| Profile | Planning meaning |
|---------|------------------|
| `light` | Mechanical, local, deterministic work without meaningful design choice or state risk. |
| `standard` | Ordinary, well-defined implementation using established patterns, with contained impact and straightforward verification. |
| `strong` | Architecture, persistence/schema/data changes, concurrency, retries/idempotency, security/auth, public contracts, recovery, or other cross-cutting work. |
| `max` | Rare work where high uncertainty, large blast radius, and difficult or costly recovery all coincide. If only one or two apply, use `strong`; split an oversized phase first. |

**Documentation authoring is `max`-only.** A phase that authors or substantively
rewrites documentation content — README, `ARCHITECTURE.md`, `workflow.md`, `CLAUDE.md`,
`CONTRIBUTING.md`, `CHANGELOG`, or files under `docs/` — must be split into its own phase
carrying `**Profile:** \`max\``, even when the edit looks mechanical. This is a fixed
override of the "max is rare" guidance, not a risk judgement. It does not apply to
reading documentation for context, or to source comments and docstrings, which keep
whatever profile their surrounding work warrants. `worker/plan_resolver.py` enforces this
in the plan contract and rejects a pending documentation-authoring phase whose profile is
absent or not `max` before any model is invoked.

The planner must not inspect global/provider configuration, model catalogs, effort
levels, token usage, or prices to make this choice, and must not put a concrete
provider/model/effort into the plan. Runtime routing translates the semantic label for
the provider selected at process startup.

- The value must be one non-empty backtick-quoted name, at most once per phase. Empty,
  unquoted, duplicated, or unknown selected-provider profiles fail before a model call.
- Runtime also supports manually authored custom profile names defined in global
  configuration. The planning agent uses the four canonical semantic profiles above.
- Missing lines remain valid only as a compatibility fallback for untouched legacy
  plans; they inherit the workflow `implementation` default (`standard`). They are not
  valid output for a newly authored or newly extended plan.

The runner resolves the active plan phase (first phase with an unchecked task) and the
implementation agent executes that selected phase; it does not pick a different one.

## Migration / legacy behavior

- **No global config present:** legacy mode. Provider comes from env/CLI; models come
  from `CLAUDE_MODEL`/`CODEX_MODEL`. A warning points to the expected path and suggests
  `samocode install`.
- **Valid config present:** profile model/effort is authoritative. Legacy
  `CLAUDE_MODEL`/`CODEX_MODEL` are ignored (they only apply when the file is absent).
- **Path and timeout env overrides** (`CLAUDE_PATH`, `CODEX_PATH`, `*_TIMEOUT`, etc.)
  remain supported in both modes.
- Untouched legacy plans without a `Profile` line keep working (inherit the
  `implementation` default); newly authored phases must declare one explicitly.

## Per-iteration logging

Every iteration logs the resolved provider, profile, model, effort, workflow phase,
optional plan phase, and the profile-selection source (workflow override, phase
default, global default, or plan-phase explicit).

The same immutable routing details are injected into child Session Context. The child
must execute the selected target and active implementation-plan phase without reading
the config again or overriding provider/model/effort.
