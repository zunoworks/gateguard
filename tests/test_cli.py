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


def _make_repo(tmp_path: Path, name: str = "repo") -> Path:
    import subprocess

    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


def test_anchor_detects_wholesale_log_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The chain alone can't catch a full rewrite; an anchor can."""
    from gateguard import log as log_mod
    from gateguard.cli import cmd_anchor, cmd_audit
    from gateguard.log import log_event

    repo = _make_repo(tmp_path)
    monkeypatch.chdir(repo)

    log_event("Edit", {"file_path": "/tmp/a.py", "old_string": "x"}, "passed", "allow")
    log_event("Edit", {"file_path": "/tmp/b.py", "old_string": "y"}, "passed", "allow")

    parser = build_parser()
    assert cmd_anchor(parser.parse_args(["anchor"])) == 0
    assert "ANCHOR" in capsys.readouterr().out
    assert cmd_audit(parser.parse_args(["audit", "--verify"])) == 0
    capsys.readouterr()

    # Wholesale rewrite: delete the log, regenerate a fresh (valid!) chain.
    log_mod.GATE_LOG_PATH.unlink()
    log_event("Edit", {"file_path": "/tmp/c.py", "old_string": "z"}, "passed", "allow")

    rc = cmd_audit(parser.parse_args(["audit", "--verify"]))
    out = capsys.readouterr().out
    assert rc == 1
    assert "chain: VERIFIED" in out  # the chain itself looks fine...
    assert "mismatched" in out       # ...the anchor exposes the rewrite


def test_anchor_refuses_broken_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from gateguard import log as log_mod
    from gateguard.cli import cmd_anchor
    from gateguard.log import log_event

    repo = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    log_event("Edit", {"file_path": "/tmp/a.py", "old_string": "x"}, "passed", "allow")
    rec = json.loads(log_mod.GATE_LOG_PATH.read_text(encoding="utf-8").strip())
    rec["summary"] = "doctored"
    log_mod.GATE_LOG_PATH.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    parser = build_parser()
    assert cmd_anchor(parser.parse_args(["anchor"])) == 1
    assert "Refusing" in capsys.readouterr().out


def test_stats_reports_insurance_coverage(capsys: pytest.CaptureFixture[str]) -> None:
    from gateguard.cli import cmd_stats
    from gateguard.log import log_event

    log_event("Bash", {"command": "rm -rf build"}, "fact_force_destructive", "deny")
    log_event(
        "Bash", {"command": "rm -rf build"}, "destructive_insured", "allow",
        extra={"certificate": {"covered_unbacked": 3}},
    )
    log_event("Write", {"file_path": "/tmp/a"}, "write_backup", "observe",
              extra={"backup": {"blob": "ab"}})

    parser = build_parser()
    rc = cmd_stats(parser.parse_args(["stats"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Insured destructions: 1" in out
    assert "covering 3 file(s)" in out
    assert "Write overwrites backed up: 1" in out


def test_diff_shows_changes_since_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from gateguard.cli import cmd_diff
    from gateguard.snapshot import capture_snapshot

    repo = _make_repo(tmp_path)
    (repo / "f.txt").write_text("before\n", encoding="utf-8")
    snap = capture_snapshot(str(repo), command="rm -rf .")
    assert snap is not None
    (repo / "f.txt").write_text("after\n", encoding="utf-8")

    parser = build_parser()
    rc = cmd_diff(parser.parse_args(["diff", snap.id]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "-before" in out and "+after" in out

    assert cmd_diff(parser.parse_args(["diff", "nope"])) == 1
    capsys.readouterr()


def test_snapshots_prune_by_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess
    import time as _time

    from gateguard import state as state_mod
    from gateguard.cli import cmd_snapshots
    from gateguard.snapshot import capture_snapshot, list_snapshots

    repo = _make_repo(tmp_path)
    snap = capture_snapshot(str(repo), command="rm -rf .")
    assert snap is not None

    # Age the record beyond the retention window.
    path = state_mod.STATE_DIR / "snapshots.jsonl"
    import json

    rec = json.loads(path.read_text(encoding="utf-8").strip())
    rec["ts"] = _time.time() - 90 * 86400
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    parser = build_parser()
    rc = cmd_snapshots(parser.parse_args(["snapshots", "--prune", "--keep-days", "30"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Pruned 1" in out
    assert list_snapshots() == []
    refs = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref", "refs/gateguard/snapshots/"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert refs.strip() == ""


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
