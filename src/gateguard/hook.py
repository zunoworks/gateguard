"""PreToolUse hook entrypoint — the fact-forcing gate.

Reads Claude Code's PreToolUse JSON payload from stdin and decides
whether to deny the action (emitting a gate message the LLM must handle
before retrying) or allow it (emitting nothing).

Gate taxonomy:
  Gate 1 — read_before_edit
      Edit on a file that hasn't been Read this session is denied.
  Gate 2 — fact_force
      First Edit/Write per file is denied with a fact-presentation prompt.
      Destructive Bash commands are always gated (not once-per-session).
      Routine Bash is gated once per session.
"""

from __future__ import annotations

import fnmatch
import json
import re
import sys
from typing import Any

from .config import Config, load_config
from .log import log_event
from .messages import (
    bash_destructive_gate,
    bash_routine_gate,
    edit_gate_msg,
    write_gate_msg,
)
from .state import load_state, update_state


# Built-in destructive command pattern. Users can extend via
# `.gateguard.yml` → destructive_bash_extra.
BUILTIN_DESTRUCTIVE_BASH = re.compile(
    r"\b(rm\s+-rf|git\s+reset\s+--hard|git\s+checkout\s+--|git\s+clean\s+-f"
    r"|drop\s+table|delete\s+from|truncate|git\s+push\s+--force"
    r"|dd\s+if=)\b",
    re.IGNORECASE,
)


def _compile_destructive(cfg: Config) -> re.Pattern[str]:
    if not cfg.destructive_bash_extra:
        return BUILTIN_DESTRUCTIVE_BASH
    joined = "|".join(re.escape(p) for p in cfg.destructive_bash_extra)
    return re.compile(
        BUILTIN_DESTRUCTIVE_BASH.pattern + "|" + joined,
        re.IGNORECASE,
    )


def _is_ignored(path_or_cmd: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path_or_cmd, pat) for pat in patterns)


def _deny(reason: str, *, tool_name: str, tool_input: dict[str, Any], gate_type: str) -> None:
    log_event(tool_name, tool_input, gate_type, "deny")
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def _handle_edit_or_write(
    tool_name: str,
    tool_input: dict[str, Any],
    cfg: Config,
) -> None:
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return

    if _is_ignored(file_path, cfg.ignore_paths):
        log_event(tool_name, tool_input, "ignored", "allow")
        return

    state = load_state()

    # Gate 1: Read-before-Edit (only applies to Edit — Write creates new files)
    if tool_name == "Edit" and cfg.gates.read_before_edit:
        read_files = set(state.get("read_files", []))
        if file_path not in read_files:
            _deny(
                f"File {file_path} has not been Read yet. "
                "Read the file before editing it.",
                tool_name=tool_name,
                tool_input=tool_input,
                gate_type="read_before_edit",
            )
            return

    # Gate 2: Fact-forcing (first action per file)
    fact_enabled = (
        cfg.gates.fact_force_edit if tool_name == "Edit"
        else cfg.gates.fact_force_write
    )
    if not fact_enabled:
        log_event(tool_name, tool_input, "disabled", "allow")
        return

    gated = set(state.get("gated_targets", []))
    if file_path in gated:
        log_event(tool_name, tool_input, "passed", "allow")
        return

    def _mark(s: dict) -> dict:
        targets = list(s.get("gated_targets", []))
        if file_path not in targets:
            targets.append(file_path)
        s["gated_targets"] = targets
        return s

    update_state(_mark)

    if tool_name == "Edit":
        msg = edit_gate_msg(file_path, cfg.messages)
    else:
        msg = write_gate_msg(file_path, cfg.messages)

    _deny(msg, tool_name=tool_name, tool_input=tool_input, gate_type="fact_force")


def _handle_bash(tool_input: dict[str, Any], cfg: Config) -> None:
    command = tool_input.get("command", "")
    if not command:
        return

    if _is_ignored(command, cfg.ignore_paths):
        log_event("Bash", tool_input, "ignored", "allow")
        return

    destructive_re = _compile_destructive(cfg)
    if cfg.gates.fact_force_bash_destructive and destructive_re.search(command):
        _deny(
            bash_destructive_gate(cfg.messages),
            tool_name="Bash",
            tool_input=tool_input,
            gate_type="fact_force_destructive",
        )
        return

    if not cfg.gates.fact_force_bash_routine:
        log_event("Bash", tool_input, "disabled", "allow")
        return

    state = load_state()
    gated = set(state.get("gated_targets", []))
    if "__bash_session__" in gated:
        log_event("Bash", tool_input, "passed", "allow")
        return

    def _mark(s: dict) -> dict:
        targets = list(s.get("gated_targets", []))
        if "__bash_session__" not in targets:
            targets.append("__bash_session__")
        s["gated_targets"] = targets
        return s

    update_state(_mark)
    _deny(
        bash_routine_gate(cfg.messages),
        tool_name="Bash",
        tool_input=tool_input,
        gate_type="fact_force_routine",
    )


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}

    cfg = load_config()
    if not cfg.enabled:
        log_event(tool_name, tool_input, "disabled_global", "allow")
        return

    if tool_name in ("Edit", "Write"):
        _handle_edit_or_write(tool_name, tool_input, cfg)
        return

    if tool_name == "Bash":
        _handle_bash(tool_input, cfg)
        return

    # Unknown / untracked tool — allow.
    log_event(tool_name, tool_input, "untracked", "allow")


if __name__ == "__main__":
    main()
