"""Append-only JSONL log of gate events — the audit trail.

One line per gate check. Read by `gateguard logs`, `gateguard audit`,
and analytics scripts. Path: ~/.gateguard/gate_log.jsonl

v0.7.0 turns the log into an audit trail:

  - Every record carries the session id and cwd, so a trail can answer
    "in which session, in which project, did the AI do this".
  - Records are hash-chained: each record stores `prev` (the previous
    record's hash) and `h` (SHA-256 over its own canonical JSON minus
    `h`). Editing or deleting any line breaks every hash after it —
    `gateguard audit --verify` walks the chain and reports the first
    break. Pre-v0.7.0 records have no `h`; verification counts them as
    legacy and restarts the chain after them.
  - `extra` carries structured context (snapshot refs, risk tiers) for
    records that have it.

Appends take a file lock so concurrent hook invocations cannot
interleave the chain. Logging stays best-effort — it must never raise.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from . import state as _state
from .state import STATE_DIR

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows
    fcntl = None

GATE_LOG_PATH = STATE_DIR / "gate_log.jsonl"

GENESIS = "genesis"


def _canonical(record: dict) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def record_hash(record: dict) -> str:
    """Hash of a record's canonical form, excluding its own `h` field."""
    body = {k: v for k, v in record.items() if k != "h"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _last_hash(path) -> str:
    """The `h` of the log's last line, or GENESIS.

    A last line without `h` (legacy, pre-v0.7.0) also yields GENESIS —
    the chain restarts after legacy records, and verification mirrors
    this exact rule.
    """
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192))
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return GENESIS
    lines = [ln for ln in chunk.splitlines() if ln.strip()]
    if not lines:
        return GENESIS
    try:
        rec = json.loads(lines[-1])
    except json.JSONDecodeError:
        return GENESIS
    h = rec.get("h") if isinstance(rec, dict) else None
    return h if isinstance(h, str) and h else GENESIS


def log_event(
    tool_name: str,
    tool_input: dict[str, Any],
    gate_type: str,
    action: str,
    extra: dict[str, Any] | None = None,
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
        elif tool_name in ("Read", "Grep", "Glob"):
            target = tool_input.get("file_path", "") or tool_input.get("path", "") or ""
            pattern = str(tool_input.get("pattern", "") or "")[:120]
            summary = f"target={target} pattern={pattern!r}" if pattern else f"target={target}"

        record: dict[str, Any] = {
            "ts": time.time(),
            "session": _state._resolve_session_id(),
            "cwd": os.getcwd(),
            "tool": tool_name,
            "gate": gate_type,
            "action": action,
            "summary": summary[:300],
        }
        if extra:
            record["extra"] = extra

        lock_path = GATE_LOG_PATH.with_suffix(".lock")
        lock_handle = None
        try:
            if fcntl is not None:
                lock_handle = lock_path.open("a")
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)

            record["prev"] = _last_hash(GATE_LOG_PATH)
            record["h"] = record_hash(record)
            with GATE_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            if lock_handle is not None:
                try:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_handle.close()
    except OSError:
        pass
