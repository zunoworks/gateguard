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

    # PostToolUse registered
    post = hooks.get("PostToolUse", [])
    assert any(
        h.get("command") == "gateguard-read-tracker"
        for group in post for h in group.get("hooks", [])
    )
