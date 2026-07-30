"""Config loading — defaults + YAML override."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateguard.config import CONFIG_FILENAME, Config, load_config


def test_defaults_when_no_config(tmp_path: Path) -> None:
    cfg = load_config(start=tmp_path)
    assert isinstance(cfg, Config)
    assert cfg.enabled is True
    assert cfg.gates.read_before_edit is True
    assert cfg.ignore_paths == []


def test_yaml_override(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    (tmp_path / CONFIG_FILENAME).write_text(
        "enabled: false\n"
        "gates:\n"
        "  read_before_edit: false\n"
        "destructive_bash_extra:\n"
        "  - 'supabase db reset'\n"
        "ignore_paths:\n"
        "  - '.venv/**'\n",
        encoding="utf-8",
    )
    cfg = load_config(start=tmp_path)
    assert cfg.enabled is False
    assert cfg.gates.read_before_edit is False
    assert "supabase db reset" in cfg.destructive_bash_extra
    assert ".venv/**" in cfg.ignore_paths


def test_bughunt_commands_extra_parsed(tmp_path: Path) -> None:
    # v0.6.1 (issue #1): Flutter/Dart recognizer extension
    pytest.importorskip("yaml")
    (tmp_path / CONFIG_FILENAME).write_text(
        "bughunt_commands_extra:\n"
        "  - 'flutter test'\n"
        "  - 'dart test'\n",
        encoding="utf-8",
    )
    cfg = load_config(start=tmp_path)
    assert cfg.bughunt_commands_extra == ["flutter test", "dart test"]


def test_bughunt_commands_extra_defaults_empty(tmp_path: Path) -> None:
    assert load_config(start=tmp_path).bughunt_commands_extra == []


def test_bughunt_extra_extends_recognizer() -> None:
    from gateguard.bughunt import is_bughunt_command
    from gateguard.hook import _compile_bughunt

    cfg = Config()
    cfg.bughunt_commands_extra = ["flutter test", "dart test", "flutter analyze"]
    pattern = _compile_bughunt(cfg)

    assert is_bughunt_command("flutter test --coverage", pattern)
    assert is_bughunt_command("dart test test/unit", pattern)
    assert is_bughunt_command("pytest -q", pattern)  # built-ins preserved
    assert not is_bughunt_command("flutter run", pattern)  # runは検証ではない
    assert not is_bughunt_command("flutter test")  # 拡張なしでは従来どおり非認識


def test_bughunt_extra_none_when_unconfigured() -> None:
    from gateguard.hook import _compile_bughunt

    assert _compile_bughunt(Config()) is None  # 未設定なら組み込みそのまま
