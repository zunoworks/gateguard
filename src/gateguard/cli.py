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
from .trail import load_trail, render_report, verify_anchors, verify_chain


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
    anchors_ok, anchor_problems = verify_anchors(chain)
    failed = (not chain.ok) or bool(anchor_problems)

    if args.verify:
        print(chain.describe())
        if anchors_ok or anchor_problems:
            print(f"anchors: {anchors_ok} verified, {len(anchor_problems)} mismatched")
        for problem in anchor_problems:
            print(f"  ✗ {problem}")
        return 1 if failed else 0

    records = load_trail(session=args.session, tail=args.tail)
    print(render_report(records, chain, fmt=args.format))
    for problem in anchor_problems:
        print(f"ANCHOR MISMATCH: {problem}")
    return 1 if failed else 0


# ---------- anchor ----------

def cmd_anchor(args: argparse.Namespace) -> int:
    """Pin the current chain head outside the log file.

    The hash chain detects edited/deleted lines but not a wholesale
    rewrite (hashing involves no secret). An anchor stores {head,
    record count} as a git object under refs/gateguard/anchors/ in the
    CURRENT repo; --push sends the anchors to a remote the rewriter
    cannot reach. The printed line also lands in the session transcript
    — a copy that lives with the user, not with the log.
    """
    import subprocess

    from .snapshot import GIT_TIMEOUT_SEC

    chain = verify_chain()
    if not chain.ok:
        print(f"Refusing to anchor a broken chain: {chain.describe()}")
        return 1
    if chain.chained == 0:
        print("Nothing to anchor: no chained records yet.")
        return 1

    payload = json.dumps(
        {"ts": time.time(), "head": chain.head, "records": chain.chained}
    )
    try:
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            input=payload, capture_output=True, text=True,
            timeout=GIT_TIMEOUT_SEC, check=True,
        ).stdout.strip()
        ref = (
            "refs/gateguard/anchors/"
            + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            + "-" + chain.head[:8]
        )
        subprocess.run(
            ["git", "update-ref", ref, blob],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC, check=True,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        print(f"Anchor failed (not a git repo?): {exc}")
        return 1

    print(f"ANCHOR {chain.head} @ {chain.chained} records -> {ref}")
    if args.push:
        try:
            subprocess.run(
                ["git", "push", args.push,
                 "refs/gateguard/anchors/*:refs/gateguard/anchors/*"],
                capture_output=True, text=True, timeout=60, check=True,
            )
            print(f"Pushed anchors to {args.push}.")
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            print(f"Anchor stored locally but push failed: {exc}")
            return 1
    return 0


# ---------- snapshots ----------

def _prune_snapshots(keep_days: int) -> int:
    """Drop snapshot refs + records older than keep_days. Refs whose
    repo is gone are dropped from the listing silently."""
    import subprocess

    from . import state as state_mod
    from .snapshot import GIT_TIMEOUT_SEC

    cutoff = time.time() - keep_days * 86400
    records = list_snapshots()
    kept, pruned = [], 0
    for rec in records:
        if float(rec.get("ts", 0) or 0) >= cutoff:
            kept.append(rec)
            continue
        pruned += 1
        ref, root = rec.get("ref", ""), rec.get("repo_root", "")
        if ref and root:
            try:
                subprocess.run(
                    ["git", "-C", root, "update-ref", "-d", ref],
                    capture_output=True, timeout=GIT_TIMEOUT_SEC,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
    path = state_mod.STATE_DIR / "snapshots.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept),
        encoding="utf-8",
    )
    print(f"Pruned {pruned} snapshot(s) older than {keep_days} day(s); kept {len(kept)}.")
    return 0


def cmd_snapshots(args: argparse.Namespace) -> int:
    if args.prune:
        return _prune_snapshots(args.keep_days)
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


# ---------- diff ----------

def cmd_diff(args: argparse.Namespace) -> int:
    """Show what changed since a snapshot — the look-before-you-restore."""
    import subprocess

    from .snapshot import worktree_tree

    matches = [r for r in list_snapshots() if r.get("id") == args.id]
    if not matches:
        print(f"No snapshot with id {args.id}. See `gateguard snapshots`.")
        return 1
    rec = matches[-1]
    root = rec.get("repo_root", ".")
    # Diff tree-to-tree: writing the live worktree as a throwaway tree
    # makes untracked files show as changes, not as deletions.
    current = worktree_tree(root)
    if not current:
        print("diff failed: could not capture the current worktree state")
        return 1
    try:
        proc = subprocess.run(
            ["git", "-C", root, "--no-pager", "diff",
             rec.get("commit", ""), current],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"diff failed: {exc}")
        return 1
    if proc.returncode != 0:
        print(proc.stderr.strip() or "diff failed")
        return 1
    out = proc.stdout.strip()
    print(out if out else "No differences — the worktree matches the snapshot.")
    return 0


# ---------- stats ----------

def cmd_stats(_: argparse.Namespace) -> int:
    """Near-miss metrics: what the gate caught, what insurance covered."""
    records = load_trail()
    if not records:
        print("No trail records yet.")
        return 0

    sessions = {str(r.get("session", "")) for r in records}
    denies: dict[str, int] = {}
    allows: dict[str, int] = {}
    observes = 0
    unbacked_covered = 0
    write_backups = 0
    for r in records:
        action, gate = r.get("action"), str(r.get("gate", "?"))
        if action == "deny":
            denies[gate] = denies.get(gate, 0) + 1
        elif action == "allow":
            allows[gate] = allows.get(gate, 0) + 1
        elif action == "observe":
            if gate == "write_backup":
                write_backups += 1
            else:
                observes += 1
        cert = (r.get("extra") or {}).get("certificate") or {}
        unbacked_covered += int(cert.get("covered_unbacked", 0) or 0)

    print(f"GateGuard stats — {len(records)} records, {len(sessions)} session(s)\n")
    print("Denies (ceremonies forced):")
    for gate, n in sorted(denies.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5}  {gate}")
    if not denies:
        print("      (none)")
    print("\nAllows:")
    for gate, n in sorted(allows.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5}  {gate}")
    print(f"\nObserved investigation events: {observes}")
    insured = allows.get("destructive_insured", 0)
    print(f"Insured destructions: {insured}")
    print(f"  … covering {unbacked_covered} file(s) that existed ONLY in the "
          "working tree")
    print(f"Write overwrites backed up: {write_backups}")
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

    p_anchor = sub.add_parser(
        "anchor",
        help="pin the audit-trail chain head into the current git repo "
        "(refs/gateguard/anchors/); --push sends anchors to a remote",
    )
    p_anchor.add_argument("--push", metavar="REMOTE",
                          help="push anchors to this git remote (e.g. origin)")
    p_anchor.set_defaults(func=cmd_anchor)

    p_snap = sub.add_parser(
        "snapshots",
        help="list pre-destruction snapshots and their rollback commands",
    )
    p_snap.add_argument("--tail", type=int, default=20, help="last N snapshots (default: 20)")
    p_snap.add_argument("--prune", action="store_true",
                        help="delete snapshots older than --keep-days")
    p_snap.add_argument("--keep-days", type=int, default=30,
                        help="retention window for --prune (default: 30)")
    p_snap.set_defaults(func=cmd_snapshots)

    p_diff = sub.add_parser(
        "diff", help="diff the worktree against a snapshot (before restoring)"
    )
    p_diff.add_argument("id", help="snapshot id from `gateguard snapshots`")
    p_diff.set_defaults(func=cmd_diff)

    p_stats = sub.add_parser(
        "stats", help="near-miss metrics: denies, insured destructions, backups"
    )
    p_stats.set_defaults(func=cmd_stats)

    p_reset = sub.add_parser("reset", help="clear in-session state")
    p_reset.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
