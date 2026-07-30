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
import time
from typing import Any

from .audit import (
    evidence_level,
    grant_scope_pass,
    is_trivial_change,
    risk_tier,
    valid_scope_pass,
)
from .bughunt import (
    BUGHUNT_COMMANDS,
    bughunt_gate_should_fire,
    is_bughunt_command,
    is_debounced_edit,
    is_trivial_file,
    mark_gate_fired,
    record_bughunt,
    record_edit,
    update_recent_file_edit,
)
from .config import Config, load_config
from .log import log_event
from .readonly import is_readonly_bash
from .messages import (
    bash_destructive_gate,
    bash_routine_gate,
    bughunt_gate_msg,
    edit_gate_msg,
    elevated_addendum,
    high_risk_addendum,
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


def _compile_bughunt(cfg: Config) -> re.Pattern[str] | None:
    """v0.6.1 (issue #1): extend the test/build recognizer via
    ``bughunt_commands_extra`` — the Flutter/Dart case. None means
    "use the built-in recognizer"."""
    if not cfg.bughunt_commands_extra:
        return None
    joined = "|".join(re.escape(p) for p in cfg.bughunt_commands_extra)
    return re.compile(
        BUGHUNT_COMMANDS.pattern + "|" + joined,
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
) -> bool:
    """Returns True if the op was allowed AND should count toward bughunt tracking.

    Returns False for deny paths and for "ignored" paths (ops on .venv/** etc.
    are real edits but should not make the bughunt budget tick).
    """
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return False

    if _is_ignored(file_path, cfg.ignore_paths):
        log_event(tool_name, tool_input, "ignored", "allow")
        return False

    state = load_state()
    now = time.time()

    # Gate 0 (v0.4.0): Bughunt gate — opt-in via cfg.gates.bughunt_gate.
    # Fires before any other gate so the reminder arrives at the start of the
    # next batch of edits, not after one more deny cycle.
    if cfg.gates.bughunt_gate and bughunt_gate_should_fire(state, now=now):
        update_state(lambda s: mark_gate_fired(s, now))
        _deny(
            bughunt_gate_msg(cfg.messages),
            tool_name=tool_name,
            tool_input=tool_input,
            gate_type="bughunt_gate",
        )
        return False

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
            return False

    # Gate 2: Fact-forcing (first action per file)
    fact_enabled = (
        cfg.gates.fact_force_edit if tool_name == "Edit"
        else cfg.gates.fact_force_write
    )
    if not fact_enabled:
        log_event(tool_name, tool_input, "disabled", "allow")
        return True

    gated = set(state.get("gated_targets", []))
    if file_path in gated:
        # The gate already fired for this file; this is the post-ceremony
        # retry (or a later edit). v0.6.0: the completed ceremony verifies
        # the surrounding scope — grant the directory pass here.
        if cfg.audit.scope_pass and not valid_scope_pass(file_path, state, now):
            update_state(lambda s: grant_scope_pass(s, file_path, now))
        log_event(tool_name, tool_input, "passed", "allow")
        return True

    # v0.6.0 recognition audit — consult observed history before demanding
    # the ceremony. Order: tier → trivial → evidence → scope pass.
    tier = risk_tier(tool_name, tool_input, file_path)
    if tier == "high" and not cfg.audit.high_risk_guard:
        tier = "normal"

    if tier != "high" and cfg.audit.trivial_pass and is_trivial_change(tool_name, tool_input):
        # Comment/blank-line-only edit — no ceremony, and deliberately NOT
        # added to gated_targets: the next substantive edit is still gated.
        log_event(tool_name, tool_input, "trivial_pass", "allow")
        return False

    if tier != "high":
        level = evidence_level(file_path, state, now)
        if cfg.audit.evidence_pass and level == "deep":
            # Investigation was observed in the ledger — equivalent to the
            # ceremony. Approve and grant the directory pass.
            def _evidence_promote(s: dict) -> dict:
                targets = list(s.get("gated_targets", []))
                if file_path not in targets:
                    targets.append(file_path)
                s["gated_targets"] = targets
                return grant_scope_pass(s, file_path, now)

            update_state(_evidence_promote)
            log_event(tool_name, tool_input, "evidence_pass", "allow")
            return True
        if (cfg.audit.scope_pass and tier == "normal" and level == "touched"
                and valid_scope_pass(file_path, state, now)):
            # Read file inside a recently-verified directory. elevated
            # (signature changes) is NOT exempted — dependents cross scopes.
            def _scope_promote(s: dict) -> dict:
                targets = list(s.get("gated_targets", []))
                if file_path not in targets:
                    targets.append(file_path)
                s["gated_targets"] = targets
                return grant_scope_pass(s, file_path, now)

            update_state(_scope_promote)
            log_event(tool_name, tool_input, "scope_pass", "allow")
            return True

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

    gate_type = "fact_force"
    if tier == "high":
        # Never exempted by evidence, session state, or scope passes.
        msg += high_risk_addendum(cfg.messages)
        gate_type = "fact_force_high"
    elif tier == "elevated":
        msg += elevated_addendum(cfg.messages)

    _deny(msg, tool_name=tool_name, tool_input=tool_input, gate_type=gate_type)
    return False


def _handle_bash(tool_input: dict[str, Any], cfg: Config) -> bool:
    """Returns True if the op was allowed AND should count toward bughunt tracking."""
    command = tool_input.get("command", "")
    if not command:
        return False

    if _is_ignored(command, cfg.ignore_paths):
        log_event("Bash", tool_input, "ignored", "allow")
        return False

    # v0.6.0: read-only commands (ls, cat, grep, git status, ...) observe
    # without mutating — gating them is pure friction. Destructive
    # detection is unreachable for them by definition (a command cannot
    # be both read-only and match the destructive patterns).
    if cfg.gates.readonly_bash_bypass and is_readonly_bash(command):
        log_event("Bash", tool_input, "readonly_pass", "allow")
        return True

    # Gate 0 (v0.4.0): Bughunt gate — opt-in.
    # Skip when the command itself is a bughunt run (pytest, npm test, etc.);
    # denying the clearing command would be circular.
    if cfg.gates.bughunt_gate and not is_bughunt_command(command, _compile_bughunt(cfg)):
        state = load_state()
        now = time.time()
        if bughunt_gate_should_fire(state, now=now):
            update_state(lambda s: mark_gate_fired(s, now))
            _deny(
                bughunt_gate_msg(cfg.messages),
                tool_name="Bash",
                tool_input=tool_input,
                gate_type="bughunt_gate",
            )
            return False

    destructive_re = _compile_destructive(cfg)
    if cfg.gates.fact_force_bash_destructive and destructive_re.search(command):
        _deny(
            bash_destructive_gate(cfg.messages),
            tool_name="Bash",
            tool_input=tool_input,
            gate_type="fact_force_destructive",
        )
        return False

    if not cfg.gates.fact_force_bash_routine:
        log_event("Bash", tool_input, "disabled", "allow")
        return True

    state = load_state()
    gated = set(state.get("gated_targets", []))
    if "__bash_session__" in gated:
        log_event("Bash", tool_input, "passed", "allow")
        return True

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
    return False


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
        allowed = _handle_edit_or_write(tool_name, tool_input, cfg)
        if allowed and cfg.gates.bughunt_gate:
            file_path = (tool_input or {}).get("file_path", "")
            # v0.4.1: docs/plaintext edits never count; re-edits to the same
            # file within BUGHUNT_DEBOUNCE_SEC don't add to the budget either.
            if file_path and not is_trivial_file(file_path):
                now = time.time()

                def _update(s: dict) -> dict:
                    if not is_debounced_edit(s, file_path, now=now):
                        record_edit(s, now)
                    update_recent_file_edit(s, file_path, now)
                    return s

                update_state(_update)
        return

    if tool_name == "Bash":
        allowed = _handle_bash(tool_input, cfg)
        if allowed and cfg.gates.bughunt_gate:
            command = (tool_input or {}).get("command", "")
            if is_bughunt_command(command, _compile_bughunt(cfg)):
                update_state(lambda s: record_bughunt(s, time.time()))
        return

    # Unknown / untracked tool — allow.
    log_event(tool_name, tool_input, "untracked", "allow")


if __name__ == "__main__":
    main()
