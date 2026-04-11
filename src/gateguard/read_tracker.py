"""PostToolUse(Read) hook — tracks which files have been Read this session.

Used by Gate 1 (Read-before-Edit): if a file hasn't been Read, the first
Edit attempt is denied.

Stdin: {"tool_name": "Read", "tool_input": {"file_path": "/path/to/file"}, ...}
Stdout: nothing (PostToolUse hooks don't affect tool execution).
"""

from __future__ import annotations

import json
import sys

from .state import update_state


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    file_path = (data.get("tool_input") or {}).get("file_path", "")
    if not file_path:
        return

    def add_read_file(state: dict) -> dict:
        read_files = list(state.get("read_files", []))
        if file_path not in read_files:
            read_files.append(file_path)
        state["read_files"] = read_files
        return state

    update_state(add_read_file)


if __name__ == "__main__":
    main()
