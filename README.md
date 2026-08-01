# `agent-mv`

Rename a project directory and take every AI agent's history with it.

```bash
agent-mv ~/projects/old-name ~/projects/new-name
```

## What problem this solves

Every coding agent keys its history to the project's path. Rename the directory
and each of them loses track of it — the conversations are still on disk, but no
agent will offer them to you again.

There are already single-agent tools for this:

| Agent | Tool |
|---|---|
| Claude Code | [`claude-mv`](https://github.com/captivus/claude-mv) |
| Codex | [`codex-mv`](https://github.com/captivus/codex-mv) |

But **you cannot just run both**. Each one moves the directory itself, so
whichever runs second fails with "Source directory not found." A project used
with two agents had no clean answer.

`agent-mv` moves the directory once, then hands each agent's tool its own job
via `--no-move`.

## How it works

```
agent-mv OLD NEW
  1. detect which agents actually have history for OLD
  2. refuse if any of their tools is missing (moving would orphan that history)
  3. refuse if an agent is running in the project
  4. mv the directory, once
  5. claude-mv --no-move OLD NEW
     codex-mv  --no-move OLD NEW
```

The per-agent logic is **not reimplemented here**. `codex-mv` alone needs to
rewrite session files, update a sqlite cache shared across every project, and
migrate a trust entry — that lives in `codex-mv`, with its own test suite, and
this calls it.

Ordering matters: the guard runs **before** the move. Once a directory has been
renamed, a live process's `/proc/<pid>/cwd` resolves to the *new* path, so a
check against the old one afterwards would find nothing.

## Usage

```bash
agent-mv [options] OLD_PATH NEW_PATH
```

| Flag | Description |
|------|-------------|
| `-n`, `--dry-run` | Show which agents are involved and what would run; change nothing |
| `-y`, `--yes` | Skip the confirmation prompt |
| `--no-color` | Disable coloured output |
| `-h`, `--help` | Show help |
| `--version` | Print the version |

```
$ agent-mv --dry-run ~/projects/pickleball-league ~/projects/kitchen-crew
Planned actions:
  Project dir : /home/alice/projects/pickleball-league -> /home/alice/projects/kitchen-crew
  Claude Code : 1 history directory  (via claude-mv)
  Codex       : 110 session(s)  (via codex-mv)
  Would run: claude-mv --no-move /home/alice/projects/pickleball-league /home/alice/projects/kitchen-crew
  Would run: codex-mv --no-move /home/alice/projects/pickleball-league /home/alice/projects/kitchen-crew
Dry-run complete - no changes made.
```

## Safety

- **Refuses if an agent's tool is missing** rather than moving the directory and
  orphaning that agent's history.
- **Refuses while an agent is running** in the project, since agents write to
  their history continuously.
- **Reports partial completion honestly.** If one agent's tool fails after the
  directory has moved, it says which agents were updated, which was not, and the
  exact command to finish the job — rather than reporting success.
- Each delegated tool keeps its own protections. `codex-mv`, for instance, still
  writes its own reversible backup and can be undone with `codex-mv --undo`.

## Adding another agent

Agents are a list at the top of the script:

```python
AGENTS = [
    {"name": "Claude Code", "tool": "claude-mv", "processes": ("claude",),
     "detect": ..., "describe": ...},
    ...
]
```

To add one: write (or find) a tool for that agent that supports `--no-move`,
then add an entry describing how to detect its history and how to spot it
running.

Deliberately not done yet for agents whose layout has not been worked out.
Gemini CLI, for example, keys history by a short project name
(`~/.gemini/history/<name>`) rather than a full path, which is a different
problem — and guessing at an abstraction for it before solving it would build
the wrong shape.

## Installation

Standalone — Python 3.8+, standard library only. Needs `claude-mv` and
`codex-mv` on `PATH` for the agents you use.

```bash
curl -sSL https://raw.githubusercontent.com/captivus/agent-mv/main/agent-mv -o ~/bin/agent-mv
chmod +x ~/bin/agent-mv
```

## Tests

```bash
uv run --no-project python tests/test_agent_mv.py
```

Every test runs against throwaway `HOME` / `CLAUDE_CONFIG_DIR` / `CODEX_HOME`
directories, so no real agent history is read or written. The end-to-end tests
drive the real `claude-mv` and `codex-mv` and assert that a renamed project's
history follows it in both; they skip if those tools are not installed.

The suite is mutation-tested: proceeding without a required tool, skipping the
live-agent guard, and delegating to only the first agent each cause failures in
the tests that should catch them.
