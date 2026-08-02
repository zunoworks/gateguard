"""GateGuard CLI — `gateguard init | logs | audit | snapshots | reset | --version`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from . import log as log_mod
from .config import CONFIG_FILENAME, default_config_yaml
from .snapshot import list_snapshots
from .state import _state_file, clear_state
from .trail import load_trail, render_report, verify_chain


CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
HOOK_COMMAND = "gateguard-hook"
READ_TRACKER_COMMAND = "gateguard-read-tracker"
# v0.7.0: the gate may capture + verify a pre-destruction snapshot inside
# the hook, so the PreToolUse budget is wider than the observer's.
PRE_HOOK_TIMEOUT_MS = 15000
HOOK_TIMEOUT_MS = 3000
# v0.6.0: the read tracker became the evidence ledger — it observes
# Grep/Glob/investigative Bash in addition to Read.
READ_TRACKER_MATCHER = "Read|Grep|Glob|Bash"


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
    has_pre = False
    for group in pre:
        if not isinstance(group, dict):
            continue
        for h in (group.get("hooks", []) or []):
            if isinstance(h, dict) and h.get("command", "").strip() == HOOK_COMMAND:
                has_pre = True
                # Pre-v0.7.0 installs registered a 3s budget — too tight
                # for snapshot capture. Upgrade in place.
                if h.get("timeout", 0) < PRE_HOOK_TIMEOUT_MS:
                    h["timeout"] = PRE_HOOK_TIMEOUT_MS
                    modified = True
    if not has_pre:
        pre.append({
            "matcher": "Edit|Write|Bash",
            "hooks": [
                {
                    "type": "command",
                    "command": HOOK_COMMAND,
                    "timeout": PRE_HOOK_TIMEOUT_MS,
                }
            ],
        })
        modified = True

    # PostToolUse: the observation hook (Gate 1 read tracking + the
    # v0.6.0 evidence ledger).
    post = hooks.setdefault("PostToolUse", [])
    has_post = False
    for group in post:
        if not isinstance(group, dict):
            continue
        for h in (group.get("hooks", []) or []):
            if isinstance(h, dict) and h.get("command", "").strip() == READ_TRACKER_COMMAND:
                has_post = True
                # v0.5.x installs registered matcher "Read" only. Upgrade
                # in place so the evidence ledger actually receives
                # Grep/Glob/Bash events after `pip install --upgrade`.
                if group.get("matcher") != READ_TRACKER_MATCHER:
                    group["matcher"] = READ_TRACKER_MATCHER
                    modified = True
    if not has_post:
        post.append({
            "matcher": READ_TRACKER_MATCHER,
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
    if not log_mod.GATE_LOG_PATH.exists():
        print(f"No log at {log_mod.GATE_LOG_PATH}")
        return 0

    lines = log_mod.GATE_LOG_PATH.read_text(encoding="utf-8").splitlines()
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


# ---------- audit ----------

def cmd_audit(args: argparse.Namespace) -> int:
    """Flight-recorder report + tamper-evidence verification."""
    chain = verify_chain()
    if args.verify:
        print(chain.describe())
        return 0 if chain.ok else 1

    records = load_trail(session=args.session, tail=args.tail)
    print(render_report(records, chain, fmt=args.format))
    return 0 if chain.ok else 1


# ---------- snapshots ----------

def cmd_snapshots(args: argparse.Namespace) -> int:
    records = list_snapshots(tail=args.tail)
    if not records:
        print("No snapshots recorded.")
        return 0
    for rec in records:
        ts = rec.get("ts", 0)
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "?"
        print(f"{when}  {rec.get('id', '?')}")
        print(f"    repo:     {rec.get('repo_root', '?')}")
        print(f"    command:  {rec.get('command', '?')}")
        print(f"    rollback: {rec.get('rollback', '?')}")
    return 0


# ---------- reset ----------

def cmd_reset(_: argparse.Namespace) -> int:
    clear_state()
    print(f"Cleared {_state_file()}")
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

    p_audit = sub.add_parser(
        "audit",
        help="flight-recorder report: investigation → decisions → insured "
        "destructions, with tamper-evidence check",
    )
    p_audit.add_argument("--verify", action="store_true",
                         help="only verify the hash chain (exit 1 on break)")
    p_audit.add_argument("--session", help="limit to one session id")
    p_audit.add_argument("--tail", type=int, default=0, help="last N records only")
    p_audit.add_argument("--format", choices=["text", "md", "jsonl"], default="text")
    p_audit.set_defaults(func=cmd_audit)

    p_snap = sub.add_parser(
        "snapshots",
        help="list pre-destruction snapshots and their rollback commands",
    )
    p_snap.add_argument("--tail", type=int, default=20, help="last N snapshots (default: 20)")
    p_snap.set_defaults(func=cmd_snapshots)

    p_reset = sub.add_parser("reset", help="clear in-session state")
    p_reset.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
