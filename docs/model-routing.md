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
| **Implementation-plan phase** | A `### Phase N` block inside a session plan. May declare its own profile. | Plan file `**Profile:** \`name\`` |

**One provider per process.** A profile never names or switches a provider; it only
selects that provider's model/effort. To run the same task under the other provider,
start a new process with a different provider selection.

**Second opinions still cross providers.** Explicit second-opinion tools (e.g. the
`/multi-review` Gemini reviewer, or a cross-provider `do2`) may call another provider.
That is orthogonal to orchestration routing.

## Config location

`$XDG_CONFIG_HOME/samocode/config.toml` when `XDG_CONFIG_HOME` is an absolute path,
otherwise `~/.config/samocode/config.toml`. Loaded once per process.

`samocode install` creates it from built-in defaults only when absent — it **never
overwrites** an existing config. A present-but-invalid config is a fatal error on both
`install` and `run` (fail-fast, never silent).

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
| implementation | standard (fallback when a plan phase omits `Profile`) |
| testing | standard |
| quality | strong |
| pr-readiness | strong |
| done | light |

Resolution order for a workflow iteration: `[workflow_overrides]` → phase default →
global `default_profile`.

## Plan-phase profiles

Inside a session plan, add a backtick-quoted profile line as the **first line** under a
phase heading to route that phase:

```markdown
### Phase 5: Provider-neutral execution resolution

**Profile:** `max`
```

- Omit the line to inherit the workflow `implementation` default (`standard`).
- The value must be a single non-empty backtick-quoted name, at most once per phase.
  Empty, unquoted, or duplicated lines fail the session before any model call.
- Built-in names: `light`, `standard`, `strong`, `max`, plus any custom profiles.

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
- Plans without a `Profile` line keep working (inherit the `implementation` default).

## Per-iteration logging

Every iteration logs the resolved provider, profile, model, effort, workflow phase,
optional plan phase, and the profile-selection source (workflow override, phase
default, global default, or plan-phase explicit).
