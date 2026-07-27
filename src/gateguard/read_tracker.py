"""PostToolUse(Read|Grep|Glob|Bash) hook — the observation side of GateGuard.

v0.1–v0.5 tracked Read calls for Gate 1 (Read-before-Edit). v0.6.0 widens
this into the evidence ledger: Grep, Glob, and investigative Bash commands
are recorded as observed investigation. The PreToolUse gate consults the
ledger and skips the fact-forcing ceremony when the investigation already
happened — recognition is judged from behavior, not from self-report.

This hook never denies anything. Recording and enforcement are kept in
separate hooks on purpose: the recorder that also polices stops being a
trustworthy recorder.

Stdin: {"tool_name": "Read|Grep|Glob|Bash", "tool_input": {...}, ...}
Stdout: nothing (PostToolUse hooks don't affect tool execution).
"""

from __future__ import annotations

import json
import sys
import time

from .audit import evidence_entry, record_evidence
from .state import update_state


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    now = time.time()

    entry = evidence_entry(tool_name, tool_input, now)
    file_path = tool_input.get("file_path", "") if tool_name == "Read" else ""

    if entry is None and not file_path:
        return

    def _record(state: dict) -> dict:
        if file_path:
            read_files = list(state.get("read_files", []))
            if file_path not in read_files:
                read_files.append(file_path)
            state["read_files"] = read_files
        if entry is not None:
            record_evidence(state, entry, now)
        return state

    update_state(_record)


if __name__ == "__main__":
    main()
