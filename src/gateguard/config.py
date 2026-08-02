"""Project-level configuration loader for GateGuard.

Reads `.gateguard.yml` from the project root (current working directory)
and falls back to defaults. All fields are optional.

Example `.gateguard.yml`:

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
        Custom gate message for Edit operations.
    ignore_paths:
      - ".venv/**"
      - "node_modules/**"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_FILENAME = ".gateguard.yml"


@dataclass
class GateConfig:
    read_before_edit: bool = True
    fact_force_edit: bool = True
    fact_force_write: bool = True
    fact_force_bash_destructive: bool = True
    fact_force_bash_routine: bool = True
    # v0.4.0: post-implementation bughunt reminder. Opt-in so that
    # `pip install --upgrade gateguard-ai` does not change behaviour for
    # existing users. Enable via `.gateguard.yml` → gates.bughunt_gate: true.
    bughunt_gate: bool = False
    # v0.6.0: read-only commands (ls / cat / grep / git status ...) skip
    # the routine gate. Default ON — it only removes friction; the
    # detector answers "not read-only" whenever unsure.
    readonly_bash_bypass: bool = True


@dataclass
class AuditConfig:
    """v0.6.0 recognition audit. Defaults ON: evidence_pass / scope_pass /
    trivial_pass only ever REDUCE denies (they answer the over-blocking
    complaints), and high_risk_guard keeps the existing deny-once
    semantics while strengthening the message on sensitive paths."""

    evidence_pass: bool = True
    scope_pass: bool = True
    trivial_pass: bool = True
    high_risk_guard: bool = True


@dataclass
class InsuranceConfig:
    """v0.7.0 destructive insurance + flight-recorder trail.

    snapshot_pass ON changes the destructive gate from "always deny"
    (v0.6.x — a wall the model learns to route around) to "deny once,
    then allow the retry ONLY with a verified pre-destruction snapshot
    in hand". It never allows without captured-and-verified insurance,
    so the fail-closed path is exactly the old wall."""

    snapshot_pass: bool = True
    # Measure the blast radius (file counts, unbacked files) and put the
    # numbers in the deny message + audit trail.
    blast_recon: bool = True
    # Record observed investigation (Read/Grep/Glob/investigative Bash)
    # into the audit trail so `gateguard audit` shows what the AI
    # checked before each mutation.
    evidence_log: bool = True
    # Before an allowed Write overwrites an existing file, stash the old
    # content as a git blob (recorded in the trail with its restore
    # command). Best-effort; never blocks the Write.
    write_backup: bool = True


@dataclass
class Config:
    enabled: bool = True
    gates: GateConfig = field(default_factory=GateConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    insurance: InsuranceConfig = field(default_factory=InsuranceConfig)
    destructive_bash_extra: list[str] = field(default_factory=list)
    # v0.6.1 (issue #1): extra patterns the bughunt gate recognizes as a
    # verification run (e.g. "flutter test"). OR-joined with the built-ins,
    # mirroring destructive_bash_extra.
    bughunt_commands_extra: list[str] = field(default_factory=list)
    messages: dict[str, str] = field(default_factory=dict)
    ignore_paths: list[str] = field(default_factory=list)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = yaml.safe_load(raw) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _find_config_path(start: Path | None = None) -> Path | None:
    """Walk up from `start` (or cwd) looking for .gateguard.yml."""
    current = (start or Path.cwd()).resolve()
    for parent in (current, *current.parents):
        candidate = parent / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(start: Path | None = None) -> Config:
    """Load config from the nearest .gateguard.yml, or return defaults."""
    path = _find_config_path(start)
    if path is None:
        return Config()

    data = _load_yaml(path)
    cfg = Config()

    if "enabled" in data and isinstance(data["enabled"], bool):
        cfg.enabled = data["enabled"]

    gates_raw = data.get("gates") or {}
    if isinstance(gates_raw, dict):
        gc = cfg.gates
        for key in (
            "read_before_edit",
            "fact_force_edit",
            "fact_force_write",
            "fact_force_bash_destructive",
            "fact_force_bash_routine",
            "bughunt_gate",
            "readonly_bash_bypass",
        ):
            if key in gates_raw and isinstance(gates_raw[key], bool):
                setattr(gc, key, gates_raw[key])

    audit_raw = data.get("audit") or {}
    if isinstance(audit_raw, dict):
        ac = cfg.audit
        for key in (
            "evidence_pass",
            "scope_pass",
            "trivial_pass",
            "high_risk_guard",
        ):
            if key in audit_raw and isinstance(audit_raw[key], bool):
                setattr(ac, key, audit_raw[key])

    insurance_raw = data.get("insurance") or {}
    if isinstance(insurance_raw, dict):
        ic = cfg.insurance
        for key in ("snapshot_pass", "blast_recon", "evidence_log", "write_backup"):
            if key in insurance_raw and isinstance(insurance_raw[key], bool):
                setattr(ic, key, insurance_raw[key])

    extra = data.get("destructive_bash_extra") or []
    if isinstance(extra, list):
        cfg.destructive_bash_extra = [str(x) for x in extra if isinstance(x, (str, int))]

    bh_extra = data.get("bughunt_commands_extra") or []
    if isinstance(bh_extra, list):
        cfg.bughunt_commands_extra = [str(x) for x in bh_extra if isinstance(x, (str, int))]

    messages = data.get("messages") or {}
    if isinstance(messages, dict):
        cfg.messages = {str(k): str(v) for k, v in messages.items() if isinstance(v, str)}

    ignore = data.get("ignore_paths") or []
    if isinstance(ignore, list):
        cfg.ignore_paths = [str(x) for x in ignore if isinstance(x, str)]

    return cfg


def default_config_yaml() -> str:
    """Return the canonical `.gateguard.yml` contents written by `gateguard init`."""
    return """# GateGuard project configuration
# Docs: https://github.com/zunoworks/gateguard

enabled: true

gates:
  # Gate 1: require Read before Edit
  read_before_edit: true
  # Gate 2: fact-forcing prompts
  fact_force_edit: true
  fact_force_write: true
  fact_force_bash_destructive: true
  fact_force_bash_routine: true
  # v0.4.0: Bughunt gate — after 3 Edit/Write without a test/build run,
  # deny the next operation and remind the LLM to verify. Opt-in.
  bughunt_gate: false
  # v0.6.0: read-only commands (ls / cat / grep / git status ...) skip
  # the routine gate entirely.
  readonly_bash_bypass: true

# v0.6.0: Recognition audit — gate decisions from OBSERVED tool history.
audit:
  # Skip the fact-forcing gate when the ledger shows the target was
  # actually investigated (Read + a grep/glob/scan of its area) recently.
  evidence_pass: true
  # After a gate pass, grant the directory a 30-min pass: Read files in a
  # recently-verified directory skip the ceremony; new directories re-gate.
  scope_pass: true
  # Comment/blank-line-only edits pass without ceremony.
  trivial_pass: true
  # auth/payment/migration/.env/CI paths: never exempted by evidence, and
  # the gate message adds an explicit user-confirmation demand.
  high_risk_guard: true

# v0.7.0: Destructive insurance + flight-recorder audit trail.
insurance:
  # Destructive commands: deny once (fact ceremony), then allow the retry
  # ONLY after capturing a git snapshot that provably contains the files
  # about to be destroyed. No verified snapshot -> keep denying (v0.6.x
  # wall semantics). Rollback: git restore --source=<commit> --worktree -- .
  snapshot_pass: true
  # Measure the blast radius (file count, bytes, files that exist ONLY in
  # the working tree) and put the numbers in the deny message + trail.
  blast_recon: true
  # Record observed investigation into the audit trail so
  # `gateguard audit` shows what the AI checked before each mutation.
  evidence_log: true
  # Before an allowed Write overwrites an existing file, stash the old
  # content as a git blob; the restore command is recorded in the trail.
  write_backup: true

# Additional destructive shell patterns (regex, OR-joined with built-ins)
destructive_bash_extra: []

# v0.6.1: extra commands the bughunt gate counts as a verification run
# (OR-joined with the built-in pytest/npm test/cargo test/... recognizer)
# e.g. for Flutter/Dart projects:
#   bughunt_commands_extra:
#     - "flutter test"
#     - "dart test"
#     - "flutter analyze"
bughunt_commands_extra: []

# Override gate messages. Keys: edit, write, bash_destructive, bash_routine
messages: {}

# Paths to skip gating (glob). Matches against the file_path / command.
ignore_paths:
  - ".venv/**"
  - "node_modules/**"
  - ".git/**"
"""
