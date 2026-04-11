# GateGuard

![PyPI](https://img.shields.io/pypi/v/gateguard-ai) ![Python](https://img.shields.io/pypi/pyversions/gateguard-ai) ![License](https://img.shields.io/pypi/l/gateguard-ai) ![CI](https://github.com/zunoworks/gateguard/actions/workflows/ci.yml/badge.svg)

**A fact-forcing hook gate for Claude Code.**

GateGuard makes Claude Code pause and investigate before it edits your files.
When Claude tries to modify, create, or run something, the gate blocks the first
attempt and forces Claude to present concrete facts — who imports this file, what
the data actually looks like, what the user's instruction was — before it is
allowed to proceed.

Self-evaluation ("are you sure?") doesn't change LLM behavior. Forced
investigation does. GateGuard is the smallest thing that reliably moves that
needle.

## Evidence: A/B test results

We tested two identical Claude Code agents on the same task — creating a new
analytics module that integrates with an existing codebase.

| Metric | With GateGuard | Without GateGuard |
| --- | --- | --- |
| **Quality score** | **8.0 / 10** | **6.5 / 10** |
| Tool calls | 11 | 21 |
| Time | 114s | 142s |
| Internal subsystem integration | Yes | **Missed** |
| Zero-filled daily data | Yes | **Missing** |
| Extra metrics (avg/day) | Yes | **Missing** |

The quality gap comes from investigation depth. The gated agent was forced to
read existing code patterns before writing — so it discovered subsystem
integration points that the ungated agent simply guessed at (incorrectly).

These are the errors tests don't catch: the code runs, but the design is shallow.
Over a 10-file project, this 1.5-point-per-file gap compounds into significant
rework.

## Install

```bash
pip install gateguard
```

## Quick start

From the project directory you want to protect:

```bash
gateguard init
```

This does three things:

1. Writes `.gateguard.yml` into the current directory.
2. Registers a `PreToolUse` hook in `~/.claude/settings.json` that runs
   `gateguard-hook` on every `Edit`, `Write`, and `Bash` call.
3. Registers a `PostToolUse` hook that tracks which files have been `Read`
   (needed for the Read-before-Edit gate).

Restart Claude Code and the gate is active.

## What the gates do

| Gate | Trigger | What Claude must do |
| --- | --- | --- |
| **Read-before-Edit** | `Edit` on a file not yet `Read` this session | Read the file first |
| **Fact-force Edit** | First `Edit` per file | List importers, affected public API, verify data schemas from real records, quote the user's instruction |
| **Fact-force Write** | First `Write` per file | Name call sites, confirm no duplicate exists, verify data schemas, quote the instruction |
| **Fact-force destructive Bash** | `rm -rf`, `git reset --hard`, `drop table`, etc. | List what will be destroyed, give a rollback, quote the instruction |
| **Fact-force routine Bash** | First `Bash` per session | Quote the user's current instruction |

Each gate fires once per target per session. After the facts are presented,
the next attempt passes through.

## Why "verify data schemas"?

In our A/B test, both agents (gated and ungated) wrote code that assumed
ISO-8601 dates and bare JSON arrays. The real data used `%Y/%m/%d %H:%M` dates
and `{"schema_version": "1.0", "items": [...]}` wrappers. Both agents got this
wrong — because neither actually looked at the data.

v0.2.0 adds a new gate item: *"If this file reads/writes data files, cat one
real record and show the actual field names, structure, and date format."* This
forces the LLM to verify assumptions against reality before writing code.

## Configuration

`gateguard init` writes a `.gateguard.yml` you can edit:

```yaml
enabled: true

gates:
  read_before_edit: true
  fact_force_edit: true
  fact_force_write: true
  fact_force_bash_destructive: true
  fact_force_bash_routine: true

destructive_bash_extra:
  - "supabase db reset"
  - "prisma migrate reset"

messages:
  edit: |
    Before editing {file_path}, present:
    1. ...

ignore_paths:
  - ".venv/**"
  - "node_modules/**"
  - ".git/**"
```

## CLI

```bash
gateguard init [path] [--force] [--skip-hook]
gateguard logs [--tail N]
gateguard reset
gateguard --version
```

- `init` — write `.gateguard.yml` and register both hooks
- `logs` — print recent gate events from `~/.gateguard/gate_log.jsonl`
- `reset` — clear the in-session state (`~/.gateguard/.session_state.json`)

## How it works

The core insight: asking an LLM to evaluate itself ("did you violate any
policies?") doesn't change its behavior. It always says no. But asking it to
_gather facts_ — "list every file that imports this module" — forces it to use
`Grep` and `Read`. The act of investigation creates awareness that the
self-evaluation never did.

Every competitor in the AI guardrails space stops at **deny**. GateGuard does
**deny + force investigation + demand evidence**. The model can't proceed until
it has demonstrated understanding.

GateGuard is a Claude Code `PreToolUse` hook that:

1. **Denies** the first attempt at Edit/Write/Bash
2. **Tells the model exactly which facts to gather** (importers, public API,
   data schemas, user instruction)
3. **Allows** the retry after facts are presented

The second attempt succeeds — but now the model has context it didn't have
before, producing measurably better code.

## License

MIT — see [LICENSE](LICENSE).
