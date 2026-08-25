# Samocode architecture

Samocode is a local orchestrator around AI coding CLIs. The Python worker owns
deterministic state, routing, retries, and process lifecycle; each child provider
invocation owns one workflow action.

## Runtime layers

```text
Parent session            Samocode worker                 Child provider CLI
human interaction    ->   state + routing + retries  ->   one phase action
progress monitoring       immutable execution target      session updates + signal
```

- The parent starts and monitors the worker. It does not manually dispatch phase
  agents while an autonomous run is active.
- The worker reads session state, resolves exactly one execution target, starts the
  selected provider, and interprets the resulting signal.
- The child starts without conversational memory. It receives `workflow.md`, the
  phase agent, and injected Session Context, then commits one coherent action.

## Configuration boundaries

Samocode intentionally has two configuration scopes:

- Project `.samocode`: repository, worktree, and session paths.
- User-global `config.toml`: provider executables, semantic model profiles, and
  workflow profile overrides.

The global path is `$XDG_CONFIG_HOME/samocode/config.toml` when XDG is absolute,
otherwise `~/.config/samocode/config.toml`. Startup loads it once. Editing it cannot
change an already-running process.

Provider selection is process-wide:

```text
CLI --provider
  -> SAMOCODE_PROVIDER
  -> config default_provider
  -> legacy default (claude)
```

A selected provider must have both a registered Python adapter and a provider table
in the global config. Unselected future-provider tables remain inert.

## Semantic routing

Workflow and implementation-plan phases name semantic profiles such as `standard`,
`strong`, or `max`; they never name a provider. The selected provider's profile table
turns that name into a concrete model and optional effort.

```text
workflow phase + optional plan phase
              |
              v
workflow override -> phase default -> global default
              ^
              |
explicit plan Profile (implementation only, highest precedence)
              |
              v
selected provider profile -> model + effort
              |
              v
immutable ExecutionTarget
```

`ExecutionTarget` also carries executable, timeout, workflow phase, optional active
plan phase, and profile-selection source. The runner creates it before the retry loop;
every retry reuses the same object and command.

## Iteration lifecycle

1. Startup composes project/runtime/global configuration and fixes the provider.
2. Before changing iteration/signal state or invoking a provider, the worker checks
   that every late overview phase is backed by accepted lifecycle history.
3. The worker reads `_overview.md` and selects the workflow phase agent.
4. During implementation, the plan resolver finds the last plan referenced under
   `## Plans` and the first implementation phase with an unchecked task.
5. Routing resolves one profile and immutable execution target.
6. The adapter builds provider-native arguments from that target.
7. The runner injects authoritative routing and plan context into the child prompt.
8. The child performs one action, commits changes, updates session state, and writes
   `_signal.json`.
9. The worker validates the signal and either continues, waits, blocks, or stops.

Malformed config, invalid profile references, stale plan references, unknown phases,
and unsupported selected providers fail before a model is invoked.

## Provider adapters

`worker/adapters.py` is the extension boundary between neutral routing and a provider
CLI. Built-in adapters translate the same target differently:

- Claude: model and optional effort flags plus native phase-agent selection.
- Codex: model and one-off reasoning-effort config plus injected phase-agent text.

Adding an orchestration provider requires an adapter registration and a matching
provider section in user config. It does not require adding provider names to plans.
Explicit second-opinion skills are separate from orchestration and may call another
provider without changing the worker's selected provider.

## Agent and skill inheritance

Every packaged phase agent uses `model: inherit`. The provider CLI therefore stays on
the model selected by the worker. Planning is the only skill that authors
`**Profile:**` metadata, and it requires exactly one semantic profile on every new
phase. The planner chooses from work character and risk alone; it does not inspect
provider configuration, models, effort, or prices. Implementation consumes the
injected active phase and must not re-resolve provider/model/effort. Claude
implementation sub-agents also use `model: inherit` and inherit session effort.

## Installation

`samocode install` performs two independent operations:

1. Create the global model config from canonical defaults when absent, or validate and
   preserve it byte-for-byte when present.
2. Install skills for Claude and Codex, plus Claude phase agents and slash commands.

Checkout installs use symlinks. Package installs use copies. Existing copied skills
and commands remain protected, while Samocode-managed copied Claude agent files are
backed up to `<name>.bak` and refreshed because fixed agent models would bypass routing.

## Legacy mode

When the global config is absent, startup warns and preserves the previous behavior:
provider selection comes from CLI/environment/default, and `CLAUDE_MODEL` or
`CODEX_MODEL` supplies the model. Untouched legacy plan files without `Profile` remain
valid in both legacy and routed modes, while newly authored phases require it.

## Source map

| Concern | Source of truth |
|---------|-----------------|
| CLI entry point, orchestrator loop, bootstrap quarantine, preflight | `worker/cli.py` |
| Global TOML/defaults/bootstrap | `worker/global_config.py` |
| Startup composition/provider selection | `worker/startup.py` |
| Workflow phase/profile defaults | `worker/phases.py` |
| Active implementation plan phase | `worker/plan_resolver.py` |
| Neutral execution target | `worker/routing.py` |
| Provider CLI translation | `worker/adapters.py` |
| Context injection/retries/process execution | `worker/runner.py` |
| Pure workflow event validation | `worker/workflow_event.py` |
| Overview state parse + atomic transition + event processor | `worker/workflow_state.py` |
| Approval service and `samocode approve` CLI | `worker/approval.py` |
| Late-phase provenance and recovery anchors | `worker/lifecycle.py` |
| Session process lease | `worker/process_lease.py` |
| Audited `samocode recover final-polish` service | `worker/recovery.py` |
| Autonomous child contract | `workflow.md` |
| User model-routing reference | `docs/model-routing.md` |
