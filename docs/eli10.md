# Explain Like I'm 10

Imagine you have a really smart helper (Claude or Codex) that can read code and write code. But it forgets everything after each conversation. So we built a system where:

1. **A notebook** (`_overview.md`) keeps track of what's been done and what's next
2. **A simple loop** (Python script) wakes up the AI CLI, says "read the notebook and do the next thing", then waits
3. The AI reads the notebook, does one piece of work, writes what happened back in the notebook, and goes to sleep
4. The loop wakes it up again, and repeats until the job is done
5. **You** (through a parent session) watch the progress and answer questions when needed

That's it. The Python loop is intentionally predictable: it owns state, routing, and
retries, while the child agent makes the engineering decisions for one action.

## Which helper wakes up?

One Samocode run chooses either Claude or Codex and keeps that choice until the process
stops. Before waking the helper, the loop looks at the current workflow/plan phase and
chooses a named strength level such as `light`, `standard`, `strong`, or `max`. Your
global `config.toml` translates that friendly level into the provider's real model and
reasoning effort.

The loop writes that choice into the helper's instructions. The helper and normal
workflow sub-agents inherit it; they do not secretly choose a different model.
If you edit the global config, restart Samocode so the next process loads it.
