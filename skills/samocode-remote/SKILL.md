---
name: samocode-remote
description: Run and monitor a samocode session on a remote host over SSH while orchestrating from the local session. Use when the user says "run samocode on <host>", "continue samocode on the pod/server", or wants long-running autonomous work executed remotely with local monitoring, Q&A and plan approval.
---

# Samocode Remote

Same contract as `samocode-run`, but the worker process lives on a remote host. The
local session only starts, watches, relays human gates and stops it.

## CRITICAL: DO NOT MANUALLY ORCHESTRATE

Everything in `samocode-run` "DO NOT MANUALLY ORCHESTRATE" applies. Additionally:

- Never edit `_overview.md`, `_signal.json` or `_signal_history.jsonl`, locally or remotely
- Never run phase agents locally against a remote session
- Never run a second worker (locally or remotely) for the same session: the lease is per host,
  so a local `samocode run` on a synced copy would not see the remote lock

## Inputs

| Name | Meaning | Example |
|------|---------|---------|
| `HOST` | SSH alias of the remote host | `pod2-ssm` |
| `REMOTE_CONFIG` | Path of `.samocode` **on the host** | `~/avon-ai/.samocode` |
| `SESSION` | Session name (not path) | `eng-1720-kafka` |

Resolve `HOST` from the user's words. If a host-specific connectivity skill exists
(for escape pods: the `pod` skill), load it first: it fixes transport, login-shell
PATH, tmux naming and health checks. Do not guess hosts.

Every remote command is `ssh -o BatchMode=yes -o ConnectTimeout=25 HOST 'bash -lc "..."'`:
non-interactive SSH has no `samocode`/`claude`/`codex` on PATH without the login shell.
Shortened below as `R '...'`.

## Steps

### 1. Preflight

```bash
R 'test -f REMOTE_CONFIG && samocode --help >/dev/null && echo ok'
R 'cd ~/samocode && git rev-parse --short HEAD'      # compare with local ~/samocode
```

If the samocode commit differs from the local checkout, report it. Update only on the
user's approval: `R 'cd ~/samocode && git pull --ff-only && samocode install'`. Both
machines must run the same `master` commit; skills and `workflow.md` are read from the
host's checkout.

### 2. Check session state

```bash
R 'samocode status --config REMOTE_CONFIG --session SESSION --json'
```

Apply the `samocode-run` step 3 rules to the result:

- unknown `phase` → report, stop, do not edit
- `worker_running: true` → a worker already owns the session; monitor it (step 4), do not start another
- `phase: done` → ask what new work to do
- `blocked` contains `workflow_error` → report `last_action`/`next_action`, stop; recovery commands
  (`samocode recover ...`, `samocode check final-polish ...`) run remotely, only after user approval
- `signal: waiting` → handle the gate (step 5) before starting

Exit 1 means the config or session cannot be resolved on the host: show stderr, ask.

### 3. Start the worker in a detached tmux session

```bash
R 'tmux new-session -d -s sc-SESSION "bash -lc \"samocode run --config REMOTE_CONFIG --session SESSION [--provider X] [--timeout N] 2>&1\""; tmux set-option -t sc-SESSION remain-on-exit on'
```

- tmux session name: `sc-SESSION`. `remain-on-exit` keeps the last screen after exit
- Never wrap with `timeout`; never `nohup ... &` inside a single ssh call (dies with the shell)
- A `--task`/`--dive` first run needs the same flags as `samocode-run`
- If `tmux new-session` fails with "duplicate session": the previous run's pane is still
  there. Inspect it (`tmux capture-pane -p -t sc-SESSION | tail -n 30`), then kill it and retry

### 4. Monitor

Poll with a background sleep sized by phase (investigation/planning 60s, implementation
120-180s, quality 120s, testing 60s), then block on it:

```bash
Bash(command="sleep 120 && ssh -o BatchMode=yes HOST 'bash -lc \"samocode status --config REMOTE_CONFIG --session SESSION --flow 3\"'", run_in_background=true)
TaskOutput(task_id=..., block=true, timeout=600000)
```

Recent commits: `R 'git -C WORKING_DIR log --oneline -3'` (`working_dir` comes from status).

Report in the `samocode-run` format, adding the host:

```
Samocode Progress on HOST [HH:MM elapsed]
Phase / Last / Next / Worker / Recent commits / Flow
```

Stop conditions (same as `samocode-run`): `phase: done`, `blocked` other than `no`,
`signal: waiting`. One extra: `worker_running: false` while `signal` is `continue` or
`null` means the worker died. Read the pane tail before reporting:

```bash
R 'tmux capture-pane -p -t sc-SESSION | tail -n 40'
R 'tail -n 20 SESSION_PATH/session.log'
```

On STOP, kill pending monitor tasks (`TaskStop`) and remove the finished tmux session
(`R 'tmux kill-session -t sc-SESSION'`) once its pane has been read.

Never read the worker's full stdout; never cat `_logs/*.jsonl`.

### 5. Human gates

Auto-approve or accept suggestions ONLY when the user explicitly asked for it.

**`waiting_for: plan_approval`**

1. Find the plan: last entry under `## Plans` in `_overview.md` (`R 'grep -A3 "^## Plans" SESSION_PATH/_overview.md'`)
2. Bring it local: `scp -q HOST:SESSION_PATH/<plan>.md <scratchpad>/` and show the path
3. On approval: `R 'samocode approve --config REMOTE_CONFIG --session SESSION'` (non-zero → show stderr, ask)
4. Restart the worker (step 3)

**`waiting_for: qa_answers`**

1. `scp -q HOST:SESSION_PATH/_qa.md <scratchpad>/_qa.md`; show it with the suggestions
2. Collect answers from the user (or fill suggestions when asked); edit the local copy
3. Push it back: `scp -q <scratchpad>/_qa.md HOST:SESSION_PATH/_qa.md`
4. Restart the worker (step 3)

If the sessions directory is a git repo with a remote on both sides, use git instead of
scp for every file exchange: `git -C SESSIONS pull` before reading, commit + push after
editing `_qa.md`, then `R 'git -C SESSIONS pull --ff-only'` before restarting. Never
mix the two for the same file in one gate.

### 6. Stop on request

```bash
R 'tmux send-keys -t sc-SESSION C-c'    # graceful: worker handles KeyboardInterrupt
sleep 5
R 'tmux kill-session -t sc-SESSION'
```

Confirm with `samocode status` that `worker_running` is `false` before starting anything else.

## Common Issues

1. **`samocode: command not found`** → missing `bash -lc`, or the host has no install; check `R 'ls ~/.local/bin/samocode'`
2. **ssh hangs** → host is starved, not the network; run the host skill's health check
3. **`Another Samocode worker or recovery owns this session`** → a worker is already running; monitor it
4. **Iteration failed at once with provider errors** → provider CLI auth/version on the host; check the pane tail
5. **Status shows a stale phase after a gate** → `samocode approve`/`_qa.md` edits must land on the host before restart; verify with `samocode status` on the host, not a local copy
6. **Version mismatch** → step 1; never patch skills on the host by hand

## Example

```
User: "run samocode on pod2 for eng-1720-kafka"
→ load `pod` skill → HOST=pod2-ssm, REMOTE_CONFIG=~/avon-ai/.samocode
→ R 'samocode status --config ~/avon-ai/.samocode --session eng-1720-kafka --json'
→ R 'tmux new-session -d -s sc-eng-1720-kafka "bash -lc \"samocode run --config ~/avon-ai/.samocode --session eng-1720-kafka 2>&1\""; tmux set-option -t sc-eng-1720-kafka remain-on-exit on'
→ monitor every 120s with samocode status, relay gates, report
```
