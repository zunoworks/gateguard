"""CLI smoke tests — init subcommand writes config, --version works."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateguard import __version__
from gateguard.cli import build_parser, cmd_init


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_init_writes_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    args = parser.parse_args(["init", str(tmp_path), "--skip-hook"])
    rc = cmd_init(args)
    assert rc == 0
    assert (tmp_path / ".gateguard.yml").exists()
    assert "Wrote" in capsys.readouterr().out


def test_init_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    cmd_init(parser.parse_args(["init", str(tmp_path), "--skip-hook"]))
    cmd_init(parser.parse_args(["init", str(tmp_path), "--skip-hook"]))
    out = capsys.readouterr().out
    assert "Kept" in out


def test_init_registers_both_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """gateguard init should register both PreToolUse and PostToolUse hooks."""
    import json

    from gateguard import cli as cli_mod

    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli_mod, "CLAUDE_SETTINGS_PATH", settings_path)

    parser = build_parser()
    args = parser.parse_args(["init", str(tmp_path)])
    cmd_init(args)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks = settings.get("hooks", {})

    # PreToolUse registered
    pre = hooks.get("PreToolUse", [])
    assert any(
        h.get("command") == "gateguard-hook"
        for group in pre for h in group.get("hooks", [])
    )

    # PostToolUse registered with the v0.6.0 evidence-ledger matcher
    post = hooks.get("PostToolUse", [])
    assert any(
        h.get("command") == "gateguard-read-tracker"
        for group in post for h in group.get("hooks", [])
    )
    assert any(
        group.get("matcher") == "Read|Grep|Glob|Bash"
        for group in post
        if any(h.get("command") == "gateguard-read-tracker"
               for h in group.get("hooks", []))
    )


def test_audit_reports_and_verifies(capsys: pytest.CaptureFixture[str]) -> None:
    from gateguard.cli import cmd_audit
    from gateguard.log import log_event

    log_event("Edit", {"file_path": "/tmp/a.py", "old_string": "x"}, "passed", "allow")

    parser = build_parser()
    rc = cmd_audit(parser.parse_args(["audit"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "chain: VERIFIED" in out
    assert "file=/tmp/a.py" in out

    rc = cmd_audit(parser.parse_args(["audit", "--verify"]))
    assert rc == 0


def test_audit_verify_fails_on_tamper(capsys: pytest.CaptureFixture[str]) -> None:
    import json

    from gateguard import log as log_mod
    from gateguard.cli import cmd_audit
    from gateguard.log import log_event

    log_event("Edit", {"file_path": "/tmp/a.py", "old_string": "x"}, "passed", "allow")
    rec = json.loads(log_mod.GATE_LOG_PATH.read_text(encoding="utf-8").strip())
    rec["summary"] = "doctored"
    log_mod.GATE_LOG_PATH.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    parser = build_parser()
    rc = cmd_audit(parser.parse_args(["audit", "--verify"]))
    assert rc == 1
    assert "BROKEN" in capsys.readouterr().out


def test_snapshots_lists_rollback(capsys: pytest.CaptureFixture[str]) -> None:
    import json

    from gateguard import state as state_mod
    from gateguard.cli import cmd_snapshots

    (state_mod.STATE_DIR / "snapshots.jsonl").write_text(
        json.dumps({
            "ts": 1.0, "id": "x-ab12", "commit": "ab12", "ref": "refs/gateguard/snapshots/x-ab12",
            "repo_root": "/proj", "command": "rm -rf build",
            "rollback": "git restore --source=ab12 --worktree -- .",
        }) + "\n",
        encoding="utf-8",
    )
    parser = build_parser()
    rc = cmd_snapshots(parser.parse_args(["snapshots"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "x-ab12" in out
    assert "git restore --source=ab12" in out


def test_init_upgrades_pre_hook_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pre-v0.7.0 install (3s PreToolUse budget) is widened for snapshot capture."""
    import json

    from gateguard import cli as cli_mod

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [{
                "matcher": "Edit|Write|Bash",
                "hooks": [{"type": "command", "command": "gateguard-hook",
                           "timeout": 3000}],
            }],
        }
    }), encoding="utf-8")
    monkeypatch.setattr(cli_mod, "CLAUDE_SETTINGS_PATH", settings_path)

    parser = build_parser()
    cmd_init(parser.parse_args(["init", str(tmp_path)]))

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["timeout"] == cli_mod.PRE_HOOK_TIMEOUT_MS


def test_init_upgrades_v05_read_only_matcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A v0.5.x install (matcher \"Read\") is upgraded in place on re-init."""
    import json

    from gateguard import cli as cli_mod

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({
        "hooks": {
            "PostToolUse": [{
                "matcher": "Read",
                "hooks": [{"type": "command", "command": "gateguard-read-tracker",
                           "timeout": 3000}],
            }],
        }
    }), encoding="utf-8")
    monkeypatch.setattr(cli_mod, "CLAUDE_SETTINGS_PATH", settings_path)

    parser = build_parser()
    cmd_init(parser.parse_args(["init", str(tmp_path)]))

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    post = settings["hooks"]["PostToolUse"]
    matchers = [g.get("matcher") for g in post
                if any(h.get("command") == "gateguard-read-tracker"
                       for h in g.get("hooks", []))]
    assert matchers == ["Read|Grep|Glob|Bash"]
