"""Hook entrypoint smoke tests — deny/allow based on state."""

from __future__ import annotations

import io
import json

import pytest

from gateguard import hook
from gateguard.state import update_state


def _invoke(monkeypatch: pytest.MonkeyPatch, payload: dict) -> dict | None:
    """Run hook.main() with a fake stdin/stdout and return the emitted JSON (or None)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    hook.main()
    raw = buf.getvalue()
    return json.loads(raw) if raw.strip() else None


def test_edit_without_read_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _invoke(
        monkeypatch,
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/foo.py", "old_string": "x"}},
    )
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "not been Read" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_first_edit_after_read_is_fact_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    update_state(lambda s: {**s, "read_files": ["/tmp/foo.py"]})
    out = _invoke(
        monkeypatch,
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/foo.py", "old_string": "x"}},
    )
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "fact" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_second_edit_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    update_state(lambda s: {
        **s,
        "read_files": ["/tmp/foo.py"],
        "gated_targets": ["/tmp/foo.py"],
    })
    out = _invoke(
        monkeypatch,
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/foo.py", "old_string": "x"}},
    )
    assert out is None


def test_destructive_bash_is_always_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _invoke(
        monkeypatch,
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/doom"}},
    )
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_routine_bash_gated_once(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _invoke(
        monkeypatch,
        {"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
    )
    assert first is not None and first["hookSpecificOutput"]["permissionDecision"] == "deny"

    second = _invoke(
        monkeypatch,
        {"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
    )
    assert second is None


def test_first_write_is_fact_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _invoke(
        monkeypatch,
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/new.py", "content": "x"}},
    )
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
