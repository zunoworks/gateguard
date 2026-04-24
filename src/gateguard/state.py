"""Shared session state for GateGuard hook.

Tracks which files have been Read (for Gate 1: Read-before-Edit)
and which targets have been gated once this session (for Gate 2: Fact-forcing).

State lives at ~/.gateguard/.session_state_{session_id}.json (one file per
CLI session to prevent cross-session state leakage).
File locking prevents corruption from concurrent hook invocations.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - unavailable on POSIX
    msvcrt = None


_env_state_dir = os.environ.get("GATEGUARD_STATE_DIR", "").strip()
STATE_DIR = Path(_env_state_dir) if _env_state_dir else Path.home() / ".gateguard"

_SAFE_CHARS = re.compile(r"[^a-zA-Z0-9_\-]")
_MAX_ID_LEN = 64


def _sanitize_id(raw: str) -> str:
    """Sanitize a session ID for safe use as a filename component."""
    cleaned = _SAFE_CHARS.sub("_", raw)
    if len(cleaned) > _MAX_ID_LEN or cleaned != raw:
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
    return cleaned


def _resolve_session_id() -> str:
    """Resolve a stable, per-session identifier.

    Priority:
      1. CLAUDE_SESSION_ID (native Claude Code)
      2. ECC_SESSION_ID (everything-claude-code)
      3. CLAUDE_TRANSCRIPT_PATH (hashed — unique per session)
      4. Fallback: hash of project dir + parent PID (stable within one CLI session)
    """
    sid = os.environ.get("CLAUDE_SESSION_ID", "")
    if sid:
        return _sanitize_id(sid)

    sid = os.environ.get("ECC_SESSION_ID", "")
    if sid:
        return _sanitize_id(sid)

    transcript = os.environ.get("CLAUDE_TRANSCRIPT_PATH", "")
    if transcript:
        return hashlib.sha256(transcript.encode()).hexdigest()[:16]

    # Fallback: project dir + parent PID for per-CLI-session isolation
    project = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    ppid = os.getppid()
    fingerprint = f"{project}:{ppid}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]


def _state_file() -> Path:
    """Return the session-specific state file path."""
    sid = _resolve_session_id()
    return STATE_DIR / f".session_state_{sid}.json"


def _lock_file() -> Path:
    """Return the session-specific lock file path."""
    sid = _resolve_session_id()
    return STATE_DIR / f".session_state_{sid}.lock"

DEFAULT_STATE: dict = {
    "read_files": [],
    "gated_targets": [],
}


def _lock_handle() -> TextIO:
    lf = _lock_file()
    lf.parent.mkdir(parents=True, exist_ok=True)
    handle = lf.open("a+", encoding="utf-8")
    handle.seek(0)
    if not handle.read(1):
        handle.write("0")
        handle.flush()
    handle.seek(0)

    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    if msvcrt is not None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return handle

    handle.close()
    raise RuntimeError("File locking is not supported on this platform")


def _unlock_handle(handle: TextIO) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return
        if msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
    finally:
        handle.close()


def _read_unlocked() -> dict:
    sf = _state_file()
    if not sf.exists():
        return {"read_files": [], "gated_targets": []}
    try:
        payload = json.loads(sf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"read_files": [], "gated_targets": []}
    if not isinstance(payload, dict):
        return {"read_files": [], "gated_targets": []}
    # Preserve every key the payload carries so callers can add new fields
    # (e.g., the v0.4.0 bughunt gate counters) without state.py knowing
    # their schema. Types are enforced only on the two core fields.
    result = dict(payload)
    read_files = result.get("read_files") or []
    gated = result.get("gated_targets") or []
    result["read_files"] = list(read_files) if isinstance(read_files, list) else []
    result["gated_targets"] = list(gated) if isinstance(gated, list) else []
    return result


def _write_unlocked(state: dict) -> None:
    sf = _state_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=sf.parent,
        prefix=".session_state-",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        json.dump(state, tmp, ensure_ascii=False)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, sf)


def load_state() -> dict:
    handle = _lock_handle()
    try:
        return _read_unlocked()
    finally:
        _unlock_handle(handle)


def update_state(mutator: Callable[[dict], dict | None]) -> dict:
    handle = _lock_handle()
    try:
        current = _read_unlocked()
        updated = mutator(dict(current))
        final = current if updated is None else updated
        _write_unlocked(final)
        return final
    finally:
        _unlock_handle(handle)


def clear_state() -> None:
    handle = _lock_handle()
    try:
        try:
            _state_file().unlink(missing_ok=True)
        except OSError:
            _write_unlocked(dict(DEFAULT_STATE))
    finally:
        _unlock_handle(handle)
