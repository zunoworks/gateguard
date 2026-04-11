"""Pytest fixtures — redirect state/log paths to a tmp dir per test."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ~/.gateguard/* into a tmp dir so tests never touch the real state."""
    state_dir = tmp_path / ".gateguard"
    state_dir.mkdir()

    from gateguard import log as log_mod
    from gateguard import state as state_mod

    monkeypatch.setattr(state_mod, "STATE_DIR", state_dir)
    monkeypatch.setattr(state_mod, "STATE_FILE", state_dir / ".session_state.json")
    monkeypatch.setattr(state_mod, "LOCK_FILE", state_dir / ".session_state.lock")
    monkeypatch.setattr(log_mod, "GATE_LOG_PATH", state_dir / "gate_log.jsonl")

    return state_dir
