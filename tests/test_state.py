"""State load/update/clear round-trip."""

from __future__ import annotations

from gateguard.state import clear_state, load_state, update_state


def test_default_state_empty() -> None:
    state = load_state()
    assert state["read_files"] == []
    assert state["gated_targets"] == []


def test_update_persists() -> None:
    def add(s: dict) -> dict:
        s["gated_targets"] = ["a.py"]
        return s

    result = update_state(add)
    assert result["gated_targets"] == ["a.py"]

    reloaded = load_state()
    assert reloaded["gated_targets"] == ["a.py"]


def test_clear_removes_state() -> None:
    update_state(lambda s: {**s, "gated_targets": ["a.py"]})
    clear_state()
    assert load_state()["gated_targets"] == []
