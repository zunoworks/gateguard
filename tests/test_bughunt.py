"""GateGuard v0.4.0 — post-implementation bughunt gate tests.

Pins the contract:
- bughunt_gate is opt-in (default False). Existing users of gateguard-ai<0.4.0
  see no behaviour change after upgrade.
- When enabled, after BUGHUNT_TRIGGER_EDITS allow-path Edit/Write ops without
  a recognised bughunt command since the last edit, the next Edit/Write/Bash
  is denied.
- Running pytest / npm test / cargo test / etc. clears the gate.
- Cooldown prevents re-firing on the next operation.
- GATEGUARD_BUGHUNT_DISABLED env var is a hard bypass.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gateguard import hook
from gateguard.bughunt import (
    BUGHUNT_DEBOUNCE_SEC,
    BUGHUNT_GATE_COOLDOWN_SEC,
    bughunt_gate_should_fire,
    is_bughunt_command,
    is_bughunt_disabled,
    is_debounced_edit,
    is_trivial_file,
    mark_gate_fired,
    record_bughunt,
    record_edit,
    update_recent_file_edit,
)
from gateguard.state import load_state, update_state


# --- is_bughunt_command ------------------------------------------------------


class TestIsBughuntCommand:
    def test_pytest(self):
        assert is_bughunt_command("pytest tests/")
        assert is_bughunt_command("python -m pytest")
        assert is_bughunt_command(".venv/bin/python -m pytest -v")

    def test_node(self):
        assert is_bughunt_command("npm test")
        assert is_bughunt_command("npm run test")
        assert is_bughunt_command("pnpm test")
        assert is_bughunt_command("yarn test")
        assert is_bughunt_command("npm run check")

    def test_rust_go(self):
        assert is_bughunt_command("cargo test")
        assert is_bughunt_command("go test ./...")

    def test_lint_type(self):
        assert is_bughunt_command("npx tsc --noEmit")
        assert is_bughunt_command("ruff check src/")
        assert is_bughunt_command("mypy .")
        assert is_bughunt_command("next build")

    def test_negatives(self):
        assert not is_bughunt_command("ls")
        assert not is_bughunt_command("git status")
        assert not is_bughunt_command("echo hello")


# --- is_bughunt_disabled -----------------------------------------------------


class TestIsBughuntDisabled:
    def test_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("GATEGUARD_BUGHUNT_DISABLED", raising=False)
        assert not is_bughunt_disabled()

    def test_env_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GATEGUARD_BUGHUNT_DISABLED", "1")
        assert is_bughunt_disabled()


# --- bughunt_gate_should_fire ------------------------------------------------


class TestBughuntGateShouldFire:
    def test_no_edits(self):
        assert not bughunt_gate_should_fire({"edit_count": 0}, now=100.0)
        assert not bughunt_gate_should_fire({"edit_count": 2}, now=100.0)

    def test_three_edits_without_bughunt(self):
        state = {"edit_count": 3, "last_edit_at": 100.0, "last_bughunt_at": 0.0}
        assert bughunt_gate_should_fire(state, now=200.0)

    def test_bughunt_after_edit_clears(self):
        state = {"edit_count": 3, "last_edit_at": 100.0, "last_bughunt_at": 150.0}
        assert not bughunt_gate_should_fire(state, now=200.0)

    def test_stale_bughunt_before_edit_still_fires(self):
        """A bughunt predating the latest edit must NOT clear the gate."""
        state = {"edit_count": 4, "last_edit_at": 200.0, "last_bughunt_at": 150.0}
        assert bughunt_gate_should_fire(state, now=250.0)

    def test_cooldown(self):
        state = {
            "edit_count": 5, "last_edit_at": 200.0, "last_bughunt_at": 0.0,
            "bughunt_gate_fired_at": 100.0,
        }
        assert not bughunt_gate_should_fire(
            state, now=100.0 + BUGHUNT_GATE_COOLDOWN_SEC / 2
        )
        assert bughunt_gate_should_fire(
            state, now=100.0 + BUGHUNT_GATE_COOLDOWN_SEC + 1
        )

    def test_disabled_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GATEGUARD_BUGHUNT_DISABLED", "1")
        state = {"edit_count": 10, "last_edit_at": 200.0, "last_bughunt_at": 0.0}
        assert not bughunt_gate_should_fire(state, now=300.0)


# --- record helpers ---------------------------------------------------------


class TestRecordHelpers:
    def test_record_edit_increments(self):
        s = {}
        out = record_edit(s, 100.0)
        assert out["edit_count"] == 1
        assert out["last_edit_at"] == 100.0
        record_edit(out, 200.0)
        assert out["edit_count"] == 2
        assert out["last_edit_at"] == 200.0

    def test_record_bughunt_increments(self):
        s = {}
        out = record_bughunt(s, 500.0)
        assert out["bughunt_count"] == 1
        assert out["last_bughunt_at"] == 500.0

    def test_mark_gate_fired(self):
        s = {}
        out = mark_gate_fired(s, 777.0)
        assert out["bughunt_gate_fired_at"] == 777.0


# --- hook.main() integration -------------------------------------------------


def _invoke(monkeypatch: pytest.MonkeyPatch, payload: dict) -> dict | None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    hook.main()
    raw = buf.getvalue()
    return json.loads(raw) if raw.strip() else None


def _enable_bughunt_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Drop a .gateguard.yml that enables bughunt + turns off other gates
    for isolation. cwd is redirected to tmp_path so load_config finds it."""
    cfg = tmp_path / ".gateguard.yml"
    cfg.write_text(
        "enabled: true\n"
        "gates:\n"
        "  read_before_edit: false\n"
        "  fact_force_edit: false\n"
        "  fact_force_write: false\n"
        "  fact_force_bash_destructive: false\n"
        "  fact_force_bash_routine: false\n"
        "  bughunt_gate: true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)


class TestBughuntGateIntegration:
    def test_opt_in_default_off_no_behavioural_change(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Without .gateguard.yml → default config → bughunt_gate=False.
        Existing gates (read_before_edit, fact_force) may still fire; this
        test asserts only that the bughunt gate itself never denies, even
        with edit_count=5."""
        update_state(lambda s: {
            **s,
            "read_files": ["/tmp/foo.py"],
            "gated_targets": ["/tmp/foo.py"],
            "edit_count": 5,
            "last_edit_at": 100.0,
            "last_bughunt_at": 0.0,
        })
        monkeypatch.chdir(tmp_path)  # no .gateguard.yml → defaults
        out = _invoke(
            monkeypatch,
            {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/foo.py", "old_string": "x"}},
        )
        if out is not None:
            reason = out["hookSpecificOutput"]["permissionDecisionReason"].lower()
            assert "bughunt" not in reason, (
                f"default config must never fire bughunt gate, got: {reason}"
            )

    def test_fires_when_enabled_and_threshold_crossed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        _enable_bughunt_config(monkeypatch, tmp_path)
        update_state(lambda s: {
            **s,
            "read_files": ["/tmp/foo.py"],
            "gated_targets": ["/tmp/foo.py"],
            "edit_count": 3,
            "last_edit_at": 100.0,
            "last_bughunt_at": 0.0,
        })
        out = _invoke(
            monkeypatch,
            {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/foo.py", "old_string": "x"}},
        )
        assert out is not None
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "bughunt" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_bughunt_command_is_allowed_even_when_gate_would_fire(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        _enable_bughunt_config(monkeypatch, tmp_path)
        update_state(lambda s: {
            **s,
            "gated_targets": ["__bash_session__"],
            "edit_count": 3,
            "last_edit_at": 100.0,
            "last_bughunt_at": 0.0,
        })
        out = _invoke(
            monkeypatch,
            {"tool_name": "Bash", "tool_input": {"command": "pytest tests/"}},
        )
        assert out is None, "pytest must pass even when gate would otherwise fire"

    def test_allowed_edit_records_edit_count(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        _enable_bughunt_config(monkeypatch, tmp_path)
        update_state(lambda s: {
            **s,
            "read_files": ["/tmp/foo.py"],
            "gated_targets": ["/tmp/foo.py"],
            "edit_count": 1,
            "last_edit_at": 0.0,
            # High bughunt time keeps the gate silent during the test.
            "last_bughunt_at": 9_999_999_999.0,
        })
        _invoke(
            monkeypatch,
            {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/foo.py", "old_string": "x"}},
        )
        assert load_state()["edit_count"] == 2

    def test_allowed_bughunt_command_records_last_bughunt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        _enable_bughunt_config(monkeypatch, tmp_path)
        update_state(lambda s: {
            **s,
            "gated_targets": ["__bash_session__"],
            "edit_count": 0,
            "last_bughunt_at": 0.0,
        })
        _invoke(
            monkeypatch,
            {"tool_name": "Bash", "tool_input": {"command": "pytest tests/foo.py"}},
        )
        state = load_state()
        assert state.get("last_bughunt_at", 0.0) > 0.0
        assert state.get("bughunt_count", 0) == 1


# --- v0.4.1 noise-reduction tests -------------------------------------------


class TestIsTrivialFile:
    def test_markdown_and_plaintext(self):
        assert is_trivial_file("README.md")
        assert is_trivial_file("docs/guide.md")
        assert is_trivial_file("notes.txt")
        assert is_trivial_file("docs/intro.rst")
        assert is_trivial_file("server.log")

    def test_case_insensitive_extension(self):
        assert is_trivial_file("README.MD")
        assert is_trivial_file("NOTES.Txt")

    def test_code_files_not_trivial(self):
        assert not is_trivial_file("src/app.py")
        assert not is_trivial_file("index.ts")
        assert not is_trivial_file("main.go")
        assert not is_trivial_file("Cargo.toml")

    def test_convention_filenames(self):
        assert is_trivial_file("CHANGELOG")
        assert is_trivial_file("CHANGELOG.md")
        assert is_trivial_file("TODO")
        assert is_trivial_file("path/to/LICENSE")
        assert is_trivial_file("project/CHANGES")

    def test_empty_and_weird_paths(self):
        assert not is_trivial_file("")
        assert not is_trivial_file("src/main.py")
        # Windows-style paths should still resolve basename correctly.
        assert is_trivial_file("C:\\repo\\CHANGELOG.md")


class TestIsDebouncedEdit:
    def test_no_state_means_not_debounced(self):
        assert not is_debounced_edit({}, "/tmp/foo.py", now=1000.0)

    def test_recent_edit_within_window(self):
        state = {"recent_file_edits": {"/tmp/foo.py": 900.0}}
        assert is_debounced_edit(
            state, "/tmp/foo.py", now=1000.0, window_sec=600.0
        )

    def test_edit_outside_window(self):
        state = {"recent_file_edits": {"/tmp/foo.py": 100.0}}
        assert not is_debounced_edit(
            state, "/tmp/foo.py", now=1000.0, window_sec=600.0
        )

    def test_different_file_not_debounced(self):
        state = {"recent_file_edits": {"/tmp/foo.py": 1000.0}}
        assert not is_debounced_edit(state, "/tmp/bar.py", now=1000.0)

    def test_malformed_timestamp_treated_as_not_debounced(self):
        """Malformed state should fail open (don't block) rather than crash."""
        state = {"recent_file_edits": {"/tmp/foo.py": "not-a-number"}}
        assert not is_debounced_edit(state, "/tmp/foo.py", now=1000.0)


class TestUpdateRecentFileEdit:
    def test_records_new_file(self):
        state: dict = {}
        out = update_recent_file_edit(state, "/tmp/a.py", 1000.0)
        assert out["recent_file_edits"] == {"/tmp/a.py": 1000.0}

    def test_overwrites_existing_file_timestamp(self):
        state = {"recent_file_edits": {"/tmp/a.py": 500.0}}
        out = update_recent_file_edit(state, "/tmp/a.py", 1000.0)
        assert out["recent_file_edits"]["/tmp/a.py"] == 1000.0

    def test_prunes_entries_older_than_2x_window(self):
        state = {
            "recent_file_edits": {
                "/tmp/stale.py": 0.0,   # far in the past
                "/tmp/fresh.py": 9_000.0,
            }
        }
        out = update_recent_file_edit(
            state, "/tmp/new.py", 10_000.0, window_sec=600.0
        )
        # cutoff = 10_000 - 1200 = 8_800, so stale (0) should be pruned,
        # fresh (9_000) should survive.
        recent = out["recent_file_edits"]
        assert "/tmp/stale.py" not in recent
        assert recent.get("/tmp/fresh.py") == 9_000.0
        assert recent.get("/tmp/new.py") == 10_000.0


class TestV041HookIntegration:
    def test_markdown_edit_does_not_increment_edit_count(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """v0.4.1: .md files are trivial — editing README.md three times
        should NOT fire the bughunt gate."""
        _enable_bughunt_config(monkeypatch, tmp_path)
        update_state(lambda s: {
            **s,
            "read_files": ["/tmp/README.md"],
            "gated_targets": ["/tmp/README.md"],
            "edit_count": 0,
            "last_bughunt_at": 9_999_999_999.0,  # silence the gate regardless
        })
        for _ in range(3):
            _invoke(
                monkeypatch,
                {
                    "tool_name": "Edit",
                    "tool_input": {
                        "file_path": "/tmp/README.md",
                        "old_string": "x",
                    },
                },
            )
        assert load_state().get("edit_count", 0) == 0

    def test_same_file_within_debounce_window_counts_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Editing the same .py file three times in quick succession should
        count as 1 edit, not 3 — step-by-step refactors shouldn't trip the
        gate."""
        _enable_bughunt_config(monkeypatch, tmp_path)
        update_state(lambda s: {
            **s,
            "read_files": ["/tmp/foo.py"],
            "gated_targets": ["/tmp/foo.py"],
            "edit_count": 0,
            "last_bughunt_at": 9_999_999_999.0,
        })
        for _ in range(3):
            _invoke(
                monkeypatch,
                {
                    "tool_name": "Edit",
                    "tool_input": {
                        "file_path": "/tmp/foo.py",
                        "old_string": "x",
                    },
                },
            )
        # First edit counts, the next two are debounced → total 1.
        assert load_state().get("edit_count", 0) == 1

    def test_different_files_each_count(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Editing three different .py files should still produce
        edit_count=3 — the debounce is per-file, not per-session."""
        _enable_bughunt_config(monkeypatch, tmp_path)
        update_state(lambda s: {
            **s,
            "read_files": ["/tmp/a.py", "/tmp/b.py", "/tmp/c.py"],
            "gated_targets": ["/tmp/a.py", "/tmp/b.py", "/tmp/c.py"],
            "edit_count": 0,
            "last_bughunt_at": 9_999_999_999.0,
        })
        for fp in ("/tmp/a.py", "/tmp/b.py", "/tmp/c.py"):
            _invoke(
                monkeypatch,
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": fp, "old_string": "x"},
                },
            )
        assert load_state().get("edit_count", 0) == 3
