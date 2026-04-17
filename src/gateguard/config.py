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


@dataclass
class Config:
    enabled: bool = True
    gates: GateConfig = field(default_factory=GateConfig)
    destructive_bash_extra: list[str] = field(default_factory=list)
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
        ):
            if key in gates_raw and isinstance(gates_raw[key], bool):
                setattr(gc, key, gates_raw[key])

    extra = data.get("destructive_bash_extra") or []
    if isinstance(extra, list):
        cfg.destructive_bash_extra = [str(x) for x in extra if isinstance(x, (str, int))]

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

# Additional destructive shell patterns (regex, OR-joined with built-ins)
destructive_bash_extra: []

# Override gate messages. Keys: edit, write, bash_destructive, bash_routine
messages: {}

# Paths to skip gating (glob). Matches against the file_path / command.
ignore_paths:
  - ".venv/**"
  - "node_modules/**"
  - ".git/**"
"""
