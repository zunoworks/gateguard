"""Post-implementation bughunt gate (v0.4.0).

After a configurable number of Edit/Write operations, if no recognised
verification command (pytest / npm test / cargo test / etc.) has run since
the last edit, the next Edit/Write/Bash is denied with a bughunt reminder.

Ships **opt-in**: `.gateguard.yml` → `gates.bughunt_gate: true` turns it on.
This keeps upgraders from gateguard-ai<0.4.0 safe — behaviour only changes
for users who explicitly request it.

Environment overrides:
- ``GATEGUARD_BUGHUNT_DISABLED=1`` — disables the gate even when config enables it.
"""

from __future__ import annotations

import os
import re


# Edits that have happened without an intervening bughunt command.
BUGHUNT_TRIGGER_EDITS = 3
# Seconds between consecutive firings on the same unverified streak.
BUGHUNT_GATE_COOLDOWN_SEC = 300.0

# Commands whose presence counts as "bughunt happened". Substring/regex
# match is deliberately loose — the goal is to credit any honest
# verification attempt, not to audit test coverage. False positives are
# acceptable because the gate only triggers after 3 unverified edits.
BUGHUNT_COMMANDS = re.compile(
    r"\b(pytest|unittest|"
    r"npm\s+(run\s+)?(test|check)|pnpm\s+(run\s+)?(test|check)|yarn\s+(run\s+)?(test|check)|"
    r"cargo\s+test|go\s+test|"
    r"smoke_?test|"
    r"next\s+build|tsc\s+--noEmit|ruff\s+check|mypy)\b",
    re.IGNORECASE,
)


def is_bughunt_disabled() -> bool:
    """Environment-level kill switch.

    Independent of the ``gates.bughunt_gate`` config flag — this is for
    CI runs, emergency bypass, or users who want fact-forcing gates but
    not the bughunt reminder.
    """
    return bool(os.environ.get("GATEGUARD_BUGHUNT_DISABLED"))


def is_bughunt_command(command: str) -> bool:
    return bool(BUGHUNT_COMMANDS.search(command))


def bughunt_gate_should_fire(state: dict, *, now: float) -> bool:
    """Should the bughunt gate fire right now?

    Fires when all of:
    - edit_count >= BUGHUNT_TRIGGER_EDITS
    - last_edit_at > last_bughunt_at (no verification since the last edit)
    - bughunt_gate_fired_at is older than cooldown, or never
    """
    if is_bughunt_disabled():
        return False
    edit_count = int(state.get("edit_count", 0) or 0)
    if edit_count < BUGHUNT_TRIGGER_EDITS:
        return False
    last_edit = float(state.get("last_edit_at", 0.0) or 0.0)
    last_bughunt = float(state.get("last_bughunt_at", 0.0) or 0.0)
    if last_bughunt >= last_edit:
        return False
    last_fired = float(state.get("bughunt_gate_fired_at", 0.0) or 0.0)
    if last_fired and now - last_fired < BUGHUNT_GATE_COOLDOWN_SEC:
        return False
    return True


def record_edit(state: dict, now: float) -> dict:
    """Mutate ``state`` to record an allow-path Edit/Write event."""
    state["edit_count"] = int(state.get("edit_count", 0) or 0) + 1
    state["last_edit_at"] = now
    return state


def record_bughunt(state: dict, now: float) -> dict:
    """Mutate ``state`` to record that a bughunt command was just allowed."""
    state["bughunt_count"] = int(state.get("bughunt_count", 0) or 0) + 1
    state["last_bughunt_at"] = now
    return state


def mark_gate_fired(state: dict, now: float) -> dict:
    """Mutate ``state`` to record the gate has just fired (for cooldown)."""
    state["bughunt_gate_fired_at"] = now
    return state
