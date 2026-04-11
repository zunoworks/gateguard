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
