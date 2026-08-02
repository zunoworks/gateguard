# GateGuard

![PyPI](https://img.shields.io/pypi/v/gateguard-ai) ![Python](https://img.shields.io/pypi/pyversions/gateguard-ai) ![License](https://img.shields.io/pypi/l/gateguard-ai) ![CI](https://github.com/zunoworks/gateguard/actions/workflows/ci.yml/badge.svg) [![Shipped in ECC](https://img.shields.io/badge/Shipped_in-ECC-blueviolet)](https://github.com/affaan-m/everything-claude-code/blob/main/skills/gateguard/SKILL.md)

**A fact-forcing hook gate for Claude Code.**

> Also shipped as a skill in [everything-claude-code](https://github.com/affaan-m/everything-claude-code). The JS port lives there; this repo is the Python upstream.

> **Not to be confused** with `gateguard-personal` — an internal hook used at ZUNO WORKS with its own version series (`v4.x`). This repository is the public Python package `gateguard-ai` (`v0.x.y` series).

GateGuard makes Claude Code pause and investigate before it edits your files.
When Claude tries to modify, create, or run something without having looked
first, the gate blocks the attempt and forces Claude to gather concrete facts —
who imports this file, what the data actually looks like, what the user's
instruction was — before it is allowed to proceed.

Self-evaluation ("are you sure?") doesn't change LLM behavior. Forced
investigation does. And since **v0.6.0**, investigation is no longer demanded
and then taken on trust: an evidence ledger observes what Claude actually did
(`Read` / `Grep` / `Glob` / investigative `Bash`), and the gate opens on
observed behavior — never on claims. If Claude already did the homework, no
gate fires at all. If it didn't, the gate fires and the homework it then does
is recorded, becoming its pass on retry.

## Evidence: A/B test results

> **Measured on v0.3.0** — when every first Edit/Write was gated. v0.6.0
> keeps this exact ceremony for *uninvestigated* targets (the evidence
> ledger only skips it when the investigation observably already
> happened), so the mechanism below is still the fallback path — but
> these numbers have **not** been re-measured on v0.6.0.
>
> **Current-generation evidence lives in
> [benchmarks/painbench](benchmarks/painbench/RESULTS.md)** (2026-07-27,
> subject claude-opus-5): trap tasks built from 2026 field reports.
> Short version — on the 5 family the gate runs at near-zero friction,
> and its one measured behavioral delta is enforcing look-before-leap
> exactly at the destructive edge where 2026's real incidents happened.

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

- **Claude Fable 5** — v0.6.0's recognition-audit logic was dogfooded live
  on Fable 5 during development (via its internal twin implementation,
  gates firing on the very session that wrote it)
- **Claude Opus 4.7** — primary target through v0.4.0–v0.5.0
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
3. Registers a `PostToolUse` hook on `Read|Grep|Glob|Bash` — the evidence
   ledger (v0.6.0). It records observed investigation for the recognition
   audit and tracks Read files for the Read-before-Edit gate. Re-running
   `gateguard init` upgrades a v0.5.x "Read"-only registration in place.

Restart Claude Code and the gate is active.

## What the gates do

| Gate | Trigger | What Claude must do |
| --- | --- | --- |
| **Read-before-Edit** | `Edit` on a file not yet `Read` this session | Read the file first |
| **Fact-force Edit** | First `Edit` per file | Quote the user's instruction, list importers, detect conflicts between existing patterns and instruction (instruction wins), verify data schemas from real records |
| **Fact-force Write** | First `Write` per file | Quote the user's instruction, confirm no duplicate exists, detect conflicts (instruction wins), verify data schemas |
| **Fact-force destructive Bash** | `rm -rf`, `git reset --hard`, `drop table`, etc. | Reconcile its fact list with the blast radius GateGuard measured itself; the retry then runs only once a verified pre-destruction snapshot exists (v0.7.0) |
| **Fact-force routine Bash** | First `Bash` per session (v0.6.0: read-only commands — `ls`, `cat`, `grep`, `git status`, safe pipes — bypass this gate entirely) | Quote the user's current instruction |
| **Bughunt** (v0.4.0+, opt-in) | 3+ Edit/Write ops to non-docs files since the last test/build run | Run tests, verify the build, exercise the change on real input, check edge cases |

Each gate fires once per target per session. After the facts are presented,
the next attempt passes through.

## Recognition audit (v0.6.0)

The fact-forcing gate demands facts, then trusts the retry — which leaves
one piece of self-report inside GateGuard itself: it cannot tell whether
the investigation actually happened. v0.6.0 removes it. The read-tracker
hook becomes an **evidence ledger** (PostToolUse on `Read|Grep|Glob|Bash`)
that records observed investigation, and the gate consults the ledger
before demanding the ceremony:

- **Evidence pass** — if the target file was Read *and* its area was
  actually searched (a grep/glob/scan mentioning the file or its
  directory, within 30 min), the fact-forcing gate is skipped. The AI's
  observed behavior — not its answer — is the passport.
- **Scope pass** — passing the gate (or an evidence pass) grants the
  file's directory a 30-minute pass. Read files inside a
  recently-verified directory skip the ceremony; moving to a new
  directory re-gates. This ends the "same confirmation over and over in
  long sessions" failure mode structurally.
- **Risk tiers** — comment/blank-line-only edits pass with no ceremony
  (*trivial*). Signature changes (`def`/`class`/`import`/`export`)
  require *deep* evidence: the dependents must have been searched
  (*elevated*). Auth / payment / migration / `.env` / CI paths are never
  exempted by evidence and add an explicit user-confirmation demand
  (*high*).

With an empty ledger, behavior is exactly v0.5.0 — the audit only ever
converts "already investigated" denies into passes, plus a stronger gate
on high-impact paths. All four switches live under `audit:` in
`.gateguard.yml`.

The bughunt gate has a 300-second cooldown after firing, so one missed
reminder does not pin the session. Bypass per-session with
`GATEGUARD_BUGHUNT_DISABLED=1`.

## Destructive insurance & the flight recorder (v0.7.0)

PainBench showed where the 5-family models still fail: the destructive
edge. v0.7.0 rebuilds the destructive gate around three ideas no
deny-only guardrail has, because they all require GateGuard's evidence
ledger and observed-behavior architecture:

**1. The gate measures the blast radius itself.** The old gate asked
the AI to list what a command would destroy — and had no way to know if
the list was honest. Now GateGuard runs its own reconnaissance before
speaking and puts the numbers in the deny message:

```
GateGuard measured the blast radius itself:
- Targets: /repo/build
- Contents: 1247 files, 3.2 MB
- ⚠ 3 file(s) exist ONLY in the working tree (untracked/modified —
  no git history has them): build/cache.db, notes.md, .env.local
```

The unbacked count is the number that matters: those files exist
nowhere except the working tree. That is exactly what the 2026
incident reports lost.

**2. Verified insurance, not hopeful backups.** The v0.6.x destructive
gate was a wall — every attempt denied, forever. A wall teaches the
model to route around it (write the `rm` into a script, run the
script), and a bypassed wall protects nobody. v0.7.0 replaces it: the
first attempt pays the fact ceremony, and the retry is allowed **only
after** GateGuard captures a git snapshot of the worktree **and
verifies the files at risk are actually inside it**. No verified
snapshot → the deny stands (the fail-closed path *is* the old wall).
The snapshot is a real commit under `refs/gateguard/snapshots/` —
off every branch, safe from gc, untouched index and HEAD — and
rollback is one recorded command:

```
git restore --source=<commit> --worktree -- .
```

**3. A flight recorder, not a log.** Every gate decision is hash-chained
(each record carries the previous record's hash — edit or delete any
line and `gateguard audit --verify` reports the exact break). Records
carry session and cwd, observed investigation is mirrored into the
trail, and evidence-based passes record *which* observations justified
them. Insured destructions carry their full certificate: blast radius,
snapshot id, verification result, rollback command.

```
$ gateguard audit
GateGuard audit trail
chain: VERIFIED — 214 chained record(s)

Session a1b2c3 — /home/you/project
2026-08-02 12:01:02    obs   Read     evidence             target=src/auth.py
2026-08-02 12:01:09    obs   Grep     evidence             target=src pattern='import auth'
2026-08-02 12:01:15  allow   Edit     evidence_pass        file=src/auth.py  [justified by: read src/auth.py; grep src]
2026-08-02 12:03:40  DENY    Bash     fact_force_destructive cmd='rm -rf build'  [blast: 1247 files, 3 unbacked]
2026-08-02 12:04:02  allow   Bash     destructive_insured  cmd='rm -rf build'  [INSURED snapshot=20260802T030402Z-ab12cd34 verified=True rollback: git restore --source=ab12cd34 --worktree -- .]
```

`gateguard audit --format md` exports the same trail for an incident
ticket or compliance review; `--verify` exits non-zero on a broken
chain, so CI can require an intact trail.

**4. The bypass routes are closed, not wished away.** A deny-only wall
teaches the model to route around it. v0.7.0 closes the known routes:

- *Script smuggling*: when the command executes a file (`bash
  cleanup.sh`, `python x.py`, `./run.sh`), GateGuard scans the
  script's **content** with the same destructive patterns before it
  runs. Writing the `rm` into a script changes nothing.
- *In-language deletion*: `shutil.rmtree`, `rimraf`, `fs.rmSync`,
  `find … -delete` are destructive patterns now — `python -c` one-liners
  included.
- *Turning the gate off*: `.gateguard.yml` is a high-risk path to the
  gate itself. Editing it is never exempted by evidence and always
  demands an explicit user instruction (as `settings.json` already did).

**5. External anchors — because a hash chain alone is not custody.**
The chain detects edited or deleted lines, but a rewriter could
regenerate the whole log (hashing involves no secret). `gateguard
anchor` pins `{chain head, record count}` as a git object under
`refs/gateguard/anchors/` — `--push origin` puts it beyond the
rewriter's reach — and `gateguard audit --verify` cross-checks every
anchor, so a wholesale rewrite turns into a reported mismatch. Every
insured snapshot also embeds the chain head in its commit message as a
passive anchor.

**6. Write overwrites are insured too.** A `Write` replaces a file's
entire content; uncommitted old content would survive nowhere. Before
an allowed overwrite, GateGuard stashes the old content as a git blob
(`git hash-object -w` — deduplicated, invisible, no index/HEAD impact)
and records the one-line restore in the trail.

Honest scoping: the snapshot covers the current git worktree only.
Targets outside the repo (or non-git directories) are uninsurable — the
gate says so and keeps denying instead of pretending. Database drops,
`dd`, and force-pushes get no filesystem recon; the report marks them
opaque. Script scanning reads the first non-flag argument (`bash -e
run.sh` is missed), and local anchors can be deleted by anyone with
repo access — push them to a remote for real custody. Upgrading
pre-v0.7.0 installs: re-run `gateguard init` once to widen the
PreToolUse hook timeout for snapshot capture.

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
  readonly_bash_bypass: true  # v0.6.0 — ls/cat/grep/git status skip the routine gate

audit:                  # v0.6.0 recognition audit (all default true)
  evidence_pass: true   # skip the gate when investigation was observed
  scope_pass: true      # 30-min directory pass after a gate pass
  trivial_pass: true    # comment-only edits skip the ceremony
  high_risk_guard: true # auth/payment/migration/.env/CI: never exempted

insurance:               # v0.7.0 destructive insurance + flight recorder
  snapshot_pass: true    # deny once, then allow only with a VERIFIED snapshot
  blast_recon: true      # measure the blast radius; numbers go in the deny
  evidence_log: true     # mirror observed investigation into the audit trail
  write_backup: true     # stash old content as a git blob before overwrites

destructive_bash_extra:
  - "supabase db reset"
  - "prisma migrate reset"

bughunt_commands_extra:   # v0.6.1 — teach the bughunt gate your stack's
  - "flutter test"        # test/build commands (OR-joined with the
  - "dart test"           # built-in pytest/npm test/cargo test/...)
  - "flutter analyze"

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
gateguard audit [--verify] [--session ID] [--tail N] [--format text|md|jsonl]
gateguard anchor [--push REMOTE]
gateguard snapshots [--tail N] [--prune --keep-days N]
gateguard diff <snapshot-id>
gateguard stats
gateguard reset
gateguard --version
```

- `init` — write `.gateguard.yml` and register both hooks
- `logs` — print recent gate events from `~/.gateguard/gate_log.jsonl`
- `audit` — flight-recorder report (investigation → decisions → insured
  destructions) with hash-chain + anchor verification; `--verify` exits 1
  on tampering
- `anchor` — pin the chain head into `refs/gateguard/anchors/` (push with
  `--push origin` for custody a log rewriter can't reach)
- `snapshots` — list pre-destruction snapshots with their rollback
  commands; `--prune` drops refs older than the retention window
- `diff` — worktree vs. snapshot (look before you restore; untracked
  files show as changes, not deletions)
- `stats` — near-miss metrics: denies forced, insured destructions, files
  that existed only in the working tree, Write backups taken
- `reset` — clear the current session's state file (`~/.gateguard/.session_state_{id}.json`)

The GitHub Action (`action.yml`) accepts an optional `trail-dir` input:
point it at an exported trail directory and CI fails on a broken chain
or anchor mismatch.

## How it works

The core insight: asking an LLM to evaluate itself ("did you violate any
policies?") doesn't change its behavior. It always says no. But asking it to
_gather facts_ — "list every file that imports this module" — forces it to use
`Grep` and `Read`. The act of investigation creates awareness that the
self-evaluation never did.

Every competitor in the AI guardrails space stops at **deny**. GateGuard does
**deny + force investigation + verify the investigation happened**. The model
can't proceed until it has demonstrated understanding — and since v0.6.0 the
demonstration is checked against observed tool history, not taken on trust.

GateGuard is a pair of Claude Code hooks:

1. The **observation hook** (`PostToolUse`) records every `Read`, `Grep`,
   `Glob`, and investigative `Bash` into an evidence ledger. It never denies
   anything — observation and enforcement are separate hooks by design.
2. The **gate** (`PreToolUse`) consults the ledger before Edit/Write/Bash.
   Investigated target → pass, silently. Uninvestigated target → **deny**,
   with exact instructions on which facts to gather (importers, data
   schemas, user instruction).
3. The investigation the model then performs is itself recorded, so the
   retry passes on evidence — and grants the directory a 30-minute scope
   pass so verified areas stay quiet.

Either way the model ends up with context it didn't have before, producing
measurably better code — the difference is that ceremony is only paid when
recognition is actually missing.

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
