"""GateGuard CLI — `gateguard init | logs | reset | --version`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import CONFIG_FILENAME, default_config_yaml
from .log import GATE_LOG_PATH
from .state import STATE_FILE, clear_state


CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
HOOK_COMMAND = "gateguard-hook"
READ_TRACKER_COMMAND = "gateguard-read-tracker"
HOOK_TIMEOUT_MS = 3000


# ---------- init ----------

def _write_config(target_dir: Path, force: bool) -> tuple[bool, Path]:
    cfg_path = target_dir / CONFIG_FILENAME
    if cfg_path.exists() and not force:
        return False, cfg_path
    cfg_path.write_text(default_config_yaml(), encoding="utf-8")
    return True, cfg_path


def _load_settings() -> dict:
    if not CLAUDE_SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_settings(settings: dict) -> None:
    CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Back up existing settings once per init.
    if CLAUDE_SETTINGS_PATH.exists():
        backup = CLAUDE_SETTINGS_PATH.with_suffix(".json.gateguard.bak")
        if not backup.exists():
            shutil.copy2(CLAUDE_SETTINGS_PATH, backup)
    CLAUDE_SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _register_hook(settings: dict) -> bool:
    """Insert GateGuard hooks into settings. Returns True if modified."""
    hooks = settings.setdefault("hooks", {})
    modified = False

    # PreToolUse: the fact-forcing gate
    pre = hooks.setdefault("PreToolUse", [])
    has_pre = any(
        isinstance(h, dict) and h.get("command", "").strip() == HOOK_COMMAND
        for group in pre if isinstance(group, dict)
        for h in (group.get("hooks", []) or [])
    )
    if not has_pre:
        pre.append({
            "matcher": "Edit|Write|Bash",
            "hooks": [
                {
                    "type": "command",
                    "command": HOOK_COMMAND,
                    "timeout": HOOK_TIMEOUT_MS,
                }
            ],
        })
        modified = True

    # PostToolUse: track Read calls (needed for Gate 1: Read-before-Edit)
    post = hooks.setdefault("PostToolUse", [])
    has_post = any(
        isinstance(h, dict) and h.get("command", "").strip() == READ_TRACKER_COMMAND
        for group in post if isinstance(group, dict)
        for h in (group.get("hooks", []) or [])
    )
    if not has_post:
        post.append({
            "matcher": "Read",
            "hooks": [
                {
                    "type": "command",
                    "command": READ_TRACKER_COMMAND,
                    "timeout": HOOK_TIMEOUT_MS,
                }
            ],
        })
        modified = True

    return modified


def cmd_init(args: argparse.Namespace) -> int:
    target_dir = Path(args.path).resolve() if args.path else Path.cwd()
    target_dir.mkdir(parents=True, exist_ok=True)

    wrote_cfg, cfg_path = _write_config(target_dir, args.force)
    print(
        f"{'Wrote' if wrote_cfg else 'Kept'} {cfg_path}"
        + ("" if wrote_cfg else " (already exists; pass --force to overwrite)")
    )

    if args.skip_hook:
        print("Skipped Claude Code hook registration (--skip-hook)")
        return 0

    settings = _load_settings()
    registered = _register_hook(settings)
    if registered:
        _save_settings(settings)
        print(f"Registered PreToolUse hook in {CLAUDE_SETTINGS_PATH}")
    else:
        print(f"Hook already present in {CLAUDE_SETTINGS_PATH}")

    print("\nGateGuard is active. Start a new Claude Code session to pick up the hook.")
    return 0


# ---------- logs ----------

def cmd_logs(args: argparse.Namespace) -> int:
    if not GATE_LOG_PATH.exists():
        print(f"No log at {GATE_LOG_PATH}")
        return 0

    lines = GATE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    tail = lines[-args.tail :] if args.tail > 0 else lines

    for raw in tail:
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        action = rec.get("action", "?")
        gate = rec.get("gate", "?")
        tool = rec.get("tool", "?")
        summary = rec.get("summary", "")
        marker = "DENY" if action == "deny" else "pass"
        print(f"{marker:5} {tool:8} {gate:25} {summary}")
    return 0


# ---------- reset ----------

def cmd_reset(_: argparse.Namespace) -> int:
    clear_state()
    print(f"Cleared {STATE_FILE}")
    return 0


# ---------- dispatch ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateguard",
        description="A fact-forcing hook gate for Claude Code.",
    )
    parser.add_argument("--version", action="version", version=f"gateguard {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="write .gateguard.yml and register the hook")
    p_init.add_argument("path", nargs="?", help="target directory (default: cwd)")
    p_init.add_argument("--force", action="store_true", help="overwrite existing config")
    p_init.add_argument("--skip-hook", action="store_true", help="don't touch ~/.claude/settings.json")
    p_init.set_defaults(func=cmd_init)

    p_logs = sub.add_parser("logs", help="show recent gate events")
    p_logs.add_argument("--tail", type=int, default=20, help="show last N entries (default: 20)")
    p_logs.set_defaults(func=cmd_logs)

    p_reset = sub.add_parser("reset", help="clear in-session state")
    p_reset.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
