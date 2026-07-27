"""Read-only Bash detection — ported hardening battery + hook integration."""

from __future__ import annotations

from gateguard import hook as hook_mod
from gateguard.config import Config
from gateguard.readonly import is_readonly_bash
from gateguard.state import load_state


def test_readonly_basic():
    assert is_readonly_bash("ls")
    assert is_readonly_bash("ls -la")
    assert is_readonly_bash("cat /tmp/foo")
    assert is_readonly_bash("grep foo bar.txt")
    assert is_readonly_bash("rg pattern src/")
    assert is_readonly_bash("git status")
    assert is_readonly_bash("git log --oneline -5")
    assert is_readonly_bash("pwd")
    assert is_readonly_bash("echo hello")
    assert is_readonly_bash("find . -name '*.py'")
    assert is_readonly_bash("head -10 file.txt")
    assert is_readonly_bash("env")
    assert is_readonly_bash("git config --get core.editor")


def test_readonly_rejects_writes():
    assert not is_readonly_bash("rm -rf /tmp/foo")
    assert not is_readonly_bash("git reset --hard")
    assert not is_readonly_bash("mv a b")
    assert not is_readonly_bash("cp a b")
    assert not is_readonly_bash("mkdir newdir")
    assert not is_readonly_bash("touch newfile")
    assert not is_readonly_bash("find . -delete")
    assert not is_readonly_bash("find . -exec rm -rf {} +")
    assert not is_readonly_bash("env rm -rf /tmp/foo")
    assert not is_readonly_bash("git diff --output=/tmp/patch.diff")
    assert not is_readonly_bash("git diff --ext-diff")


def test_readonly_rejects_compound_shell():
    assert not is_readonly_bash("ls | xargs rm")
    assert not is_readonly_bash("cat file > other")
    assert not is_readonly_bash("ls && rm -rf foo")
    assert not is_readonly_bash("ls; echo done")
    assert not is_readonly_bash("ls || rm -rf /tmp")
    assert not is_readonly_bash("ls < input")
    assert not is_readonly_bash("echo $(rm -rf /tmp)")
    assert not is_readonly_bash("echo `rm file`")
    assert not is_readonly_bash("sudo ls")


def test_readonly_allows_safe_pipes():
    assert is_readonly_bash("grep foo file.txt | head -20")
    assert is_readonly_bash("cat f | grep x | wc -l")
    assert is_readonly_bash("git log --oneline | head -5")


def test_readonly_pipe_edge_cases_rejected():
    assert not is_readonly_bash("| grep x")           # leading pipe
    assert not is_readonly_bash("grep x |")           # trailing pipe
    assert not is_readonly_bash("grep x | rm file")   # non-readonly stage


def test_readonly_rejects_newline_smuggling():
    # A newline is a command separator — the second line must never ride
    # the first line's read-only verdict.
    assert not is_readonly_bash("ls\nrm -rf /tmp/foo")
    assert not is_readonly_bash("git status\r\ngit push --force")


def test_readonly_git_global_options():
    assert is_readonly_bash("git -C /some/path log --oneline")
    assert is_readonly_bash("git --no-pager diff")
    assert is_readonly_bash("git -c core.pager=cat log")
    assert not is_readonly_bash("git -C /some/path reset --hard")


def test_fd_exec_is_not_readonly():
    assert is_readonly_bash("fd pattern src/")
    assert not is_readonly_bash("fd . -x rm")
    assert not is_readonly_bash("fd . --exec-batch rm")


# ---------- hook integration ----------

def test_readonly_bash_skips_routine_gate(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        hook_mod, "_deny",
        lambda reason, **kw: captured.update({"reason": reason, **kw}),
    )

    allowed = hook_mod._handle_bash({"command": "git status"}, Config())
    assert allowed is True
    assert not captured
    # Read-only pass must not consume the once-per-session routine slot.
    state = load_state()
    assert "__bash_session__" not in state.get("gated_targets", [])


def test_non_readonly_still_hits_routine_gate(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        hook_mod, "_deny",
        lambda reason, **kw: captured.update({"reason": reason, **kw}),
    )

    allowed = hook_mod._handle_bash({"command": "npm install"}, Config())
    assert allowed is False
    assert captured["gate_type"] == "fact_force_routine"


def test_readonly_bypass_disabled_by_config(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        hook_mod, "_deny",
        lambda reason, **kw: captured.update({"reason": reason, **kw}),
    )

    cfg = Config()
    cfg.gates.readonly_bash_bypass = False
    allowed = hook_mod._handle_bash({"command": "git status"}, cfg)
    assert allowed is False
    assert captured["gate_type"] == "fact_force_routine"


def test_destructive_never_classified_readonly(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        hook_mod, "_deny",
        lambda reason, **kw: captured.update({"reason": reason, **kw}),
    )

    allowed = hook_mod._handle_bash({"command": "rm -rf /tmp/x"}, Config())
    assert allowed is False
    assert captured["gate_type"] == "fact_force_destructive"
