"""Append-only JSONL log of gate events.

One line per gate check. Read by `gateguard logs` and by analytics scripts.
Path: ~/.gateguard/gate_log.jsonl
"""

from __future__ import annotations

import json
import time
from typing import Any

from .state import STATE_DIR

GATE_LOG_PATH = STATE_DIR / "gate_log.jsonl"


def log_event(
    tool_name: str,
    tool_input: dict[str, Any],
    gate_type: str,
    action: str,
) -> None:
    """Append a gate event. Must never raise — logging is best-effort."""
    try:
        GATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        summary = ""
        if tool_name in ("Edit", "Write"):
            fp = tool_input.get("file_path", "")
            old = str(tool_input.get("old_string", ""))[:80]
            summary = f"file={fp} old={old!r}"
        elif tool_name == "Bash":
            cmd = str(tool_input.get("command", ""))[:200]
            summary = f"cmd={cmd!r}"

        record = {
            "ts": time.time(),
            "tool": tool_name,
            "gate": gate_type,
            "action": action,
            "summary": summary[:200],
        }
        with GATE_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
