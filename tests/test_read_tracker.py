"""PostToolUse(Read) tracker — records Read targets into session state."""

from __future__ import annotations

import io
import json

import pytest

from gateguard import read_tracker
from gateguard.state import load_state


def test_read_tracker_adds_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate Read hook stdin and verify state is updated."""
    payload = json.dumps({
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/example.py"},
    })
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))

    read_tracker.main()

    state = load_state()
    assert "/tmp/example.py" in state["read_files"]


def test_read_tracker_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading the same file twice should not duplicate the entry."""
    payload = json.dumps({
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/dup.py"},
    })

    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    read_tracker.main()

    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    read_tracker.main()

    state = load_state()
    assert state["read_files"].count("/tmp/dup.py") == 1


def test_read_tracker_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty stdin should not crash."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    read_tracker.main()

    state = load_state()
    assert state["read_files"] == []
