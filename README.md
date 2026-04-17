# GateGuard

![PyPI](https://img.shields.io/pypi/v/gateguard-ai) ![Python](https://img.shields.io/pypi/pyversions/gateguard-ai) ![License](https://img.shields.io/pypi/l/gateguard-ai) ![CI](https://github.com/zunoworks/gateguard/actions/workflows/ci.yml/badge.svg) [![Shipped in ECC](https://img.shields.io/badge/Shipped_in-ECC-blueviolet)](https://github.com/affaan-m/everything-claude-code/blob/main/skills/gateguard/SKILL.md)

**A fact-forcing hook gate for Claude Code.**

> Also shipped as a skill in [everything-claude-code](https://github.com/affaan-m/everything-claude-code). The JS port lives there; this repo is the Python upstream.

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

### How we tested

1. **Gated condition**: The tester ran Claude Code with GateGuard hooks
   physically registered in `~/.claude/settings.json`. Every `Edit`, `Write`,
   and `Bash` triggered a real `PreToolUse` deny — the LLM was forced to
   investigate before retrying. This is not prompt injection — the hook
   blocks the tool call at the Claude Code runtime level.

2. **Ungated condition**: A separate Claude Code Agent (subagent) executed the
   same task with no hooks registered. Agents do not inherit the parent
   session's hooks, so this is a genuine no-gate baseline.

3. **Same task, same codebase**: Both conditions received identical prompts
   and worked on the same source tree (reset via `git checkout` between runs).

4. **Scoring**: 5 criteria × 2 points each = 10-point rubric.
   Code structure, edge case handling, pattern compliance, test quality,
   design decisions. Scored after comparing diffs side by side.

5. **Limitations**: N=3 tasks, self-scored (potential bias). The gated tester
   had seen prior results in the same session. A clean replication would use
   a fresh session with no prior exposure to the task.

## Recommended models

- **Claude Opus 4.7** — primary target, dogfooded for v0.4.0
- **Claude Sonnet 4.6** — expected to work, not benchmarked
- **Haiku 4.5 / older** — may retry instead of investigate; YMMV

GateGuard's hooks are model-agnostic at the protocol layer, but the
quality gain depends on the model treating a `PreToolUse` deny as a
cue to gather facts, not to retry the same call.

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
| **Bughunt** (v0.4.0+, opt-in) | 3+ Edit/Write ops to non-docs files since the last test/build run | Run tests, verify the build, exercise the change on real input, check edge cases |

Each gate fires once per target per session. After the facts are presented,
the next attempt passes through.

The bughunt gate has a 300-second cooldown after firing, so one missed
reminder does not pin the session. Bypass per-session with
`GATEGUARD_BUGHUNT_DISABLED=1`.

Since **v0.4.1**, the bughunt gate skips edits to `.md` / `.txt` / `.rst` /
`.log` / `.gitignore` and conventional filenames (`CHANGELOG`, `TODO`,
`LICENSE`, ...). Repeated edits to the same file within 10 minutes count as
a single edit, so step-by-step refactors of one function don't trip the
gate. These defaults keep the signal-to-noise ratio high without needing
per-project config.

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
  bughunt_gate: false  # v0.4.0 opt-in — deny the 4th Edit/Write if tests haven't run

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
