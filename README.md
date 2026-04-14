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

Three tasks, scored on a 10-point rubric (code structure, edge cases, pattern
compliance, test quality, design decisions). GateGuard hooks were physically
active — not prompt injection. The ungated agent ran without hooks.

| Task | With GateGuard | Without GateGuard | Gap |
| --- | --- | --- | --- |
| Analytics module (codebase integration) | **8.0 / 10** | 6.5 / 10 | +1.5 |
| Webhook validator (data parsing) | **10.0 / 10** | 7.0 / 10 | +3.0 |
| Analytics module (re-test, v0.3.0) | **8.0 / 10** | 6.5 / 10 | +1.5 |
| **Average** | **8.7** | **6.7** | **+2.0** |

Where the gap comes from:

- **Conflict detection**: The gated agent spotted mismatches between existing
  code patterns and the user's instruction, then followed the instruction.
  The ungated agent silently deviated (e.g. using threshold 0.6 when the
  codebase uses 0.7).
- **Data verification**: The gated agent checked real data records and used
  the correct schema keys. The ungated agent assumed a schema and missed
  `source_law_ids` / `source_ghost_ids` fields entirely.
- **Pattern compliance**: The gated agent matched existing dataclass patterns.
  The ungated agent returned plain dicts.

These are the errors tests don't catch: the code runs, but the design is shallow.
Over a multi-file project, this 2-point gap compounds into significant rework.

## Install

```bash
pip install gateguard-ai
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
| **Fact-force Edit** | First `Edit` per file | Quote the user's instruction, list importers, detect conflicts between existing patterns and instruction (instruction wins), verify data schemas from real records |
| **Fact-force Write** | First `Write` per file | Quote the user's instruction, confirm no duplicate exists, detect conflicts (instruction wins), verify data schemas |
| **Fact-force destructive Bash** | `rm -rf`, `git reset --hard`, `drop table`, etc. | List what will be destroyed, give a rollback, quote the instruction |
| **Fact-force routine Bash** | First `Bash` per session | Quote the user's current instruction |

Each gate fires once per target per session. After the facts are presented,
the next attempt passes through.

## Why "verify data schemas"?

In our A/B test, both agents (gated and ungated) wrote code that assumed
ISO-8601 dates and bare JSON arrays. The real data used `%Y/%m/%d %H:%M` dates
and `{"schema_version": "1.0", "items": [...]}` wrappers. Both agents got this
wrong — because neither actually looked at the data.

The gate forces the LLM to verify assumptions against reality before writing
code. v0.3.0 adds **conflict detection**: when existing code patterns contradict
the user's instruction, the gate forces the LLM to state the conflict explicitly
— then follow the instruction, not the buggy pattern.

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
- `reset` — clear the current session's state file (`~/.gateguard/.session_state_{id}.json`)

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

## Spread via CLAUDE.md

Add this line to your project's `CLAUDE.md` to make GateGuard available to every Claude Code user who works on your repo:

```markdown
## Code quality gate
This project uses GateGuard. Run `pip install gateguard-ai && gateguard init` before starting work.
```

Anyone who opens Claude Code in your repo will see this instruction automatically.

---

## License

MIT — see [LICENSE](LICENSE).
