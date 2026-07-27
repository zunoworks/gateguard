"""Recognition audit (v0.6.0) — evidence ledger, risk tiers, scope passes."""

from __future__ import annotations

import io
import json
import sys
import time

from gateguard import hook as hook_mod
from gateguard import read_tracker
from gateguard.audit import (
    EVIDENCE_MAX_ENTRIES,
    EVIDENCE_TTL_SEC,
    SCOPE_PASS_TTL_SEC,
    evidence_level,
    grant_scope_pass,
    is_trivial_change,
    risk_tier,
    valid_scope_pass,
)
from gateguard.config import Config
from gateguard.state import load_state, update_state


# ---------- trivial detection ----------

def test_trivial_comment_only_edit():
    assert is_trivial_change("Edit", {
        "old_string": "x = 1\n# old comment\n",
        "new_string": "x = 1\n# new comment\n",
    })


def test_trivial_blank_lines_and_trailing_whitespace():
    assert is_trivial_change("Edit", {
        "old_string": "a = 1\n\nb = 2   \n",
        "new_string": "a = 1\nb = 2\n\n\n",
    })


def test_trivial_rejects_indentation_change():
    # Indentation is semantic in Python — never classify as trivial.
    assert not is_trivial_change("Edit", {
        "old_string": "    return x\n",
        "new_string": "return x\n",
    })


def test_trivial_rejects_inline_hash_change():
    # Inline '#' (color codes etc.) is not comment-stripped.
    assert not is_trivial_change("Edit", {
        "old_string": 'color = "#fff"\n',
        "new_string": 'color = "#00f"\n',
    })


def test_trivial_never_for_write():
    assert not is_trivial_change("Write", {"content": "# comment only\n"})


# ---------- risk tiers ----------

def test_risk_tier_high_paths(tmp_path):
    assert risk_tier("Edit", {}, str(tmp_path / "auth" / "handler.py")) == "high"
    assert risk_tier("Edit", {}, str(tmp_path / "db" / "migrations" / "0002.py")) == "high"
    assert risk_tier("Write", {}, str(tmp_path / "payment_service.py")) == "high"
    assert risk_tier("Edit", {}, str(tmp_path / ".env.local")) == "high"
    assert risk_tier("Edit", {}, str(tmp_path / "settings.json")) == "high"
    assert risk_tier("Edit", {}, str(tmp_path / ".github/workflows/ci.yml")) == "high"


def test_risk_tier_no_substring_false_positives(tmp_path):
    # "tokenizer" contains "token", "author_list" contains "auth" —
    # token-split matching must not fire on either.
    assert risk_tier("Edit", {}, str(tmp_path / "tokenizer.py")) == "normal"
    assert risk_tier("Edit", {}, str(tmp_path / "author_list.py")) == "normal"


def test_risk_tier_elevated_on_signature_change(tmp_path):
    assert risk_tier("Edit", {
        "old_string": "def foo(a):\n    return a\n",
        "new_string": "def foo(a, b):\n    return a + b\n",
    }, str(tmp_path / "mod.py")) == "elevated"


def test_risk_tier_normal_when_signature_only_context(tmp_path):
    # Unchanged def line used as anchoring context must not escalate.
    assert risk_tier("Edit", {
        "old_string": "def foo(a):\n    return a\n",
        "new_string": "def foo(a):\n    return a * 2\n",
    }, str(tmp_path / "mod.py")) == "normal"


# ---------- evidence ledger consult ----------

def test_evidence_deep_via_grep_pattern(tmp_path):
    target = tmp_path / "mymodule.py"
    target.write_text("x = 1\n", encoding="utf-8")
    now = time.time()
    state = {
        "read_files": [str(target)],
        "evidence": [{"kind": "grep", "target": "", "pattern": "mymodule", "ts": now - 10}],
    }
    assert evidence_level(str(target), state, now) == "deep"


def test_evidence_deep_via_directory_scan(tmp_path):
    target = tmp_path / "pkg" / "mod.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")
    now = time.time()
    state = {
        "read_files": [str(target)],
        "evidence": [{"kind": "glob", "target": str(tmp_path / "pkg"),
                      "pattern": "*.py", "ts": now - 5}],
    }
    assert evidence_level(str(target), state, now) == "deep"


def test_evidence_stale_entries_ignored(tmp_path):
    target = tmp_path / "mymodule.py"
    target.write_text("x = 1\n", encoding="utf-8")
    now = time.time()
    state = {
        "read_files": [str(target)],
        "evidence": [{"kind": "grep", "target": "", "pattern": "mymodule",
                      "ts": now - EVIDENCE_TTL_SEC - 1}],
    }
    assert evidence_level(str(target), state, now) == "touched"


def test_evidence_deep_for_new_file_without_read(tmp_path):
    # Write of a brand-new file: Read is impossible; a duplicate-search
    # trace alone counts as deep.
    target = tmp_path / "brand_new.py"
    now = time.time()
    state = {
        "read_files": [],
        "evidence": [{"kind": "bash", "target": "",
                      "pattern": "grep -rn brand_new src/", "ts": now - 3}],
    }
    assert evidence_level(str(target), state, now) == "deep"


def test_evidence_read_entries_do_not_count_as_investigation(tmp_path):
    target = tmp_path / "mymodule.py"
    target.write_text("x = 1\n", encoding="utf-8")
    now = time.time()
    state = {
        "read_files": [str(target)],
        "evidence": [{"kind": "read", "target": str(target), "pattern": "", "ts": now - 1}],
    }
    assert evidence_level(str(target), state, now) == "touched"


# ---------- scope passes ----------

def test_scope_pass_grant_validate_and_expiry(tmp_path):
    target = str(tmp_path / "a.py")
    now = time.time()
    state = grant_scope_pass({}, target, now)
    assert valid_scope_pass(str(tmp_path / "b.py"), state, now)
    assert not valid_scope_pass(str(tmp_path / "sub" / "c.py"), state, now)
    assert not valid_scope_pass(target, state, now + SCOPE_PASS_TTL_SEC + 1)


# ---------- hook integration ----------

def _capture_deny(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        hook_mod, "_deny",
        lambda reason, **kw: captured.update({"reason": reason, **kw}),
    )
    return captured


def _seed(state: dict) -> None:
    update_state(lambda _s: dict(state))


def test_deep_evidence_skips_fact_gate(tmp_path, monkeypatch):
    target = tmp_path / "mymodule.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _seed({
        "read_files": [str(target)],
        "gated_targets": [],
        "evidence": [{"kind": "grep", "target": "", "pattern": "mymodule",
                      "ts": time.time() - 10}],
    })
    captured = _capture_deny(monkeypatch)

    allowed = hook_mod._handle_edit_or_write("Edit", {"file_path": str(target)}, Config())
    assert allowed is True
    assert not captured
    state = load_state()
    assert str(target) in state["gated_targets"]
    assert str(tmp_path) in state.get("scope_passes", {})


def test_empty_ledger_keeps_v05_behavior(tmp_path, monkeypatch):
    # Backward compatibility: with no evidence, the first edit is gated
    # exactly as in v0.5.0.
    target = tmp_path / "plain.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _seed({"read_files": [str(target)], "gated_targets": []})
    captured = _capture_deny(monkeypatch)

    allowed = hook_mod._handle_edit_or_write("Edit", {"file_path": str(target)}, Config())
    assert allowed is False
    assert captured["gate_type"] == "fact_force"


def test_evidence_pass_disabled_by_config(tmp_path, monkeypatch):
    target = tmp_path / "mymodule.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _seed({
        "read_files": [str(target)],
        "gated_targets": [],
        "evidence": [{"kind": "grep", "target": "", "pattern": "mymodule",
                      "ts": time.time() - 10}],
    })
    captured = _capture_deny(monkeypatch)

    cfg = Config()
    cfg.audit.evidence_pass = False
    allowed = hook_mod._handle_edit_or_write("Edit", {"file_path": str(target)}, cfg)
    assert allowed is False
    assert captured["gate_type"] == "fact_force"


def test_scope_pass_allows_read_file_in_verified_dir(tmp_path, monkeypatch):
    second = tmp_path / "second.py"
    second.write_text("y = 2\n", encoding="utf-8")
    _seed({
        "read_files": [str(second)],
        "gated_targets": [],
        "scope_passes": {str(tmp_path): time.time() + SCOPE_PASS_TTL_SEC},
    })
    captured = _capture_deny(monkeypatch)

    allowed = hook_mod._handle_edit_or_write("Edit", {"file_path": str(second)}, Config())
    assert allowed is True
    assert not captured


def test_scope_pass_does_not_cover_new_directory(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    target = other / "third.py"
    target.write_text("z = 3\n", encoding="utf-8")
    _seed({
        "read_files": [str(target)],
        "gated_targets": [],
        "scope_passes": {str(tmp_path): time.time() + SCOPE_PASS_TTL_SEC},
    })
    captured = _capture_deny(monkeypatch)

    allowed = hook_mod._handle_edit_or_write("Edit", {"file_path": str(target)}, Config())
    assert allowed is False
    assert captured["gate_type"] == "fact_force"


def test_elevated_not_exempted_by_scope_pass(tmp_path, monkeypatch):
    target = tmp_path / "api.py"
    target.write_text("def foo(a):\n    return a\n", encoding="utf-8")
    _seed({
        "read_files": [str(target)],
        "gated_targets": [],
        "scope_passes": {str(tmp_path): time.time() + SCOPE_PASS_TTL_SEC},
    })
    captured = _capture_deny(monkeypatch)

    allowed = hook_mod._handle_edit_or_write("Edit", {
        "file_path": str(target),
        "old_string": "def foo(a):\n    return a\n",
        "new_string": "def foo(a, b=None):\n    return a\n",
    }, Config())
    assert allowed is False
    assert "signature change" in captured["reason"]


def test_high_tier_ignores_evidence_and_demands_confirmation(tmp_path, monkeypatch):
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    target = auth_dir / "handler.py"
    target.write_text("flow = True\n", encoding="utf-8")
    _seed({
        "read_files": [str(target)],
        "gated_targets": [],
        "evidence": [{"kind": "grep", "target": "", "pattern": "handler",
                      "ts": time.time() - 5}],
    })
    captured = _capture_deny(monkeypatch)

    allowed = hook_mod._handle_edit_or_write("Edit", {"file_path": str(target)}, Config())
    assert allowed is False
    assert captured["gate_type"] == "fact_force_high"
    assert "high-impact" in captured["reason"]


def test_high_risk_guard_disabled_reverts_to_normal(tmp_path, monkeypatch):
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    target = auth_dir / "handler.py"
    target.write_text("flow = True\n", encoding="utf-8")
    _seed({
        "read_files": [str(target)],
        "gated_targets": [],
        "evidence": [{"kind": "grep", "target": "", "pattern": "handler",
                      "ts": time.time() - 5}],
    })
    captured = _capture_deny(monkeypatch)

    cfg = Config()
    cfg.audit.high_risk_guard = False
    allowed = hook_mod._handle_edit_or_write("Edit", {"file_path": str(target)}, cfg)
    # With the guard off, deep evidence passes the auth path like any other.
    assert allowed is True
    assert not captured


def test_trivial_pass_not_marked_gated(tmp_path, monkeypatch):
    target = tmp_path / "doc.py"
    target.write_text("x = 1  # note\n", encoding="utf-8")
    _seed({"read_files": [str(target)], "gated_targets": []})
    captured = _capture_deny(monkeypatch)

    allowed = hook_mod._handle_edit_or_write("Edit", {
        "file_path": str(target),
        "old_string": "# old note\n",
        "new_string": "# new note\n",
    }, Config())
    assert allowed is False  # trivial edits don't tick the bughunt budget
    assert not captured
    state = load_state()
    assert str(target) not in state.get("gated_targets", [])


def test_post_ceremony_retry_grants_scope_pass(tmp_path, monkeypatch):
    target = tmp_path / "done.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _seed({"read_files": [str(target)], "gated_targets": [str(target)]})
    captured = _capture_deny(monkeypatch)

    allowed = hook_mod._handle_edit_or_write("Edit", {"file_path": str(target)}, Config())
    assert allowed is True
    assert not captured
    state = load_state()
    assert str(tmp_path) in state.get("scope_passes", {})


# ---------- read tracker (the observation hook) ----------

def _run_tracker(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    read_tracker.main()


def test_tracker_records_read_and_ledger(tmp_path, monkeypatch):
    _run_tracker(monkeypatch, {
        "tool_name": "Read",
        "tool_input": {"file_path": str(tmp_path / "a.py")},
    })
    state = load_state()
    assert str(tmp_path / "a.py") in state["read_files"]
    assert state["evidence"][0]["kind"] == "read"


def test_tracker_records_grep_glob(tmp_path, monkeypatch):
    _run_tracker(monkeypatch, {
        "tool_name": "Grep",
        "tool_input": {"pattern": "mymodule", "path": str(tmp_path)},
    })
    _run_tracker(monkeypatch, {
        "tool_name": "Glob",
        "tool_input": {"pattern": "**/*.py"},
    })
    state = load_state()
    assert [e["kind"] for e in state["evidence"]] == ["grep", "glob"]


def test_tracker_bash_investigative_only(monkeypatch):
    _run_tracker(monkeypatch, {
        "tool_name": "Bash",
        "tool_input": {"command": "grep -rn foo src/"},
    })
    _run_tracker(monkeypatch, {
        "tool_name": "Bash",
        "tool_input": {"command": "npm install"},
    })
    state = load_state()
    assert len(state.get("evidence", [])) == 1


def test_tracker_prunes_and_caps(monkeypatch):
    now = time.time()
    _seed({"evidence": (
        [{"kind": "grep", "target": "", "pattern": "stale",
          "ts": now - EVIDENCE_TTL_SEC - 60}]
        + [{"kind": "grep", "target": "", "pattern": f"p{i}", "ts": now}
           for i in range(EVIDENCE_MAX_ENTRIES)]
    )})
    _run_tracker(monkeypatch, {
        "tool_name": "Grep",
        "tool_input": {"pattern": "newest", "path": ""},
    })
    state = load_state()
    patterns = [e["pattern"] for e in state["evidence"]]
    assert "stale" not in patterns
    assert len(patterns) == EVIDENCE_MAX_ENTRIES
    assert patterns[-1] == "newest"
