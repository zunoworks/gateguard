"""Pre-destruction snapshots — the insurance side of the destructive gate.

v0.7.0. The destructive Bash gate used to be a wall: every attempt was
denied, forever. A wall teaches the model to route around it (write the
rm into a script, run the script) — and a bypassed wall protects nobody.
The insurance model replaces it: the first attempt still pays the full
fact ceremony, and the retry is allowed ONLY once a rollback actually
exists — a snapshot of the working tree captured the moment before the
command runs.

The snapshot is a real git commit object, built without touching the
user's index, HEAD, or working tree:

    GIT_INDEX_FILE=<tmp>  git add -A     (tracked + untracked, .gitignore respected)
    git write-tree  →  git commit-tree   (parented on HEAD when it exists)
    git update-ref refs/gateguard/snapshots/<id>

The ref keeps the commit reachable (no gc), stays off every branch, and
restores with one command: `git restore --source=<commit> --worktree -- .`

Honest scoping: the snapshot covers the current git worktree only.
`rm -rf` pointed outside the repo is not insured — capture fails or the
target simply isn't in the tree — and the gate says so instead of
pretending.

Every capture is recorded in ~/.gateguard/snapshots.jsonl for
`gateguard snapshots` and the audit trail.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import state as _state


def _snapshots_path() -> Path:
    # Resolved per call (not module-level) so test fixtures that repoint
    # STATE_DIR take effect.
    return _state.STATE_DIR / "snapshots.jsonl"


# Per-git-command timeout. The whole capture is ~4 commands; the
# PreToolUse hook registration allows 15s (cli.PRE_HOOK_TIMEOUT_MS).
GIT_TIMEOUT_SEC = 5.0

SNAPSHOT_REF_PREFIX = "refs/gateguard/snapshots/"


@dataclass
class Snapshot:
    id: str
    commit: str
    ref: str
    repo_root: str
    ts: float

    @property
    def rollback_command(self) -> str:
        return f"git restore --source={self.commit} --worktree -- ."


def _git(args: list[str], cwd: str, env: dict | None = None) -> str | None:
    """Run a git command; return stripped stdout, or None on any failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def worktree_tree(root: str) -> str | None:
    """Write the current worktree (tracked + untracked, .gitignore
    respected) as a tree object via a throwaway index. None on failure.
    Shared by snapshot capture and `gateguard diff` — the same trick
    that lets a snapshot include untracked files lets a diff show them
    as changes instead of deletions."""
    tmp_index = None
    try:
        with NamedTemporaryFile(prefix="gateguard-index-", delete=False) as tmp:
            tmp_index = tmp.name
        # git refuses an existing empty file as an index; it wants to
        # create it itself.
        os.unlink(tmp_index)

        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = tmp_index
        if _git(["add", "-A", "."], root, env) is None:
            return None
        return _git(["write-tree"], root, env) or None
    except OSError:
        return None
    finally:
        if tmp_index:
            try:
                os.unlink(tmp_index)
            except OSError:
                pass


def capture_snapshot(cwd: str | None = None, *, command: str = "") -> Snapshot | None:
    """Snapshot the git worktree containing `cwd`. None if impossible.

    Never raises: the caller (the gate) maps None to "insurance
    unavailable" and keeps denying — a failed capture must fail closed.
    """
    workdir = cwd or os.getcwd()
    root = _git(["rev-parse", "--show-toplevel"], workdir)
    if not root:
        return None

    try:
        head = _git(["rev-parse", "--verify", "--quiet", "HEAD"], root)

        tree = worktree_tree(root)
        if not tree:
            return None
        env = dict(os.environ)

        now = time.time()
        # Passive anchor: embedding the current chain head makes every
        # insured destruction pin the trail into a git object — one more
        # place a wholesale log rewrite has to reach and can't silently.
        from . import log as _log

        chain_head = _log._last_hash(_log.GATE_LOG_PATH)
        message = f"gateguard: pre-destruction snapshot\n\nchain-head: {chain_head}"
        commit_args = ["commit-tree", tree, "-m", message]
        if head:
            commit_args += ["-p", head]
        # commit-tree wants an identity even when user.name is unset.
        env["GIT_AUTHOR_NAME"] = env.get("GIT_AUTHOR_NAME", "GateGuard")
        env["GIT_AUTHOR_EMAIL"] = env.get("GIT_AUTHOR_EMAIL", "gateguard@localhost")
        env["GIT_COMMITTER_NAME"] = env.get("GIT_COMMITTER_NAME", "GateGuard")
        env["GIT_COMMITTER_EMAIL"] = env.get("GIT_COMMITTER_EMAIL", "gateguard@localhost")
        commit = _git(commit_args, root, env)
        if not commit:
            return None

        snap_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)) + "-" + commit[:8]
        ref = SNAPSHOT_REF_PREFIX + snap_id
        if _git(["update-ref", ref, commit], root) is None:
            return None

        snap = Snapshot(id=snap_id, commit=commit, ref=ref, repo_root=root, ts=now)
        _record(snap, command)
        return snap
    except OSError:
        return None


def _record(snap: Snapshot, command: str) -> None:
    """Append to snapshots.jsonl. Best-effort — never raises."""
    try:
        path = _snapshots_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": snap.ts,
            "id": snap.id,
            "commit": snap.commit,
            "ref": snap.ref,
            "repo_root": snap.repo_root,
            "command": str(command)[:300],
            "rollback": snap.rollback_command,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# Files above this size are not blob-backed-up before a Write — the
# hook must stay fast, and multi-hundred-MB assets are not the "precious
# uncommitted source" this insurance exists for.
WRITE_BACKUP_MAX_BYTES = 10 * 1024 * 1024


def backup_file_blob(file_path: str) -> dict | None:
    """Stash a file's current content as a git blob before a Write
    overwrites it (v0.7.0 write insurance).

    A Write replaces the whole file; if the old content was uncommitted
    it would exist nowhere afterwards. `git hash-object -w` stores it in
    the object database in O(bytes) with automatic dedup — no working
    tree, index, or HEAD impact. Returns {"blob", "restore"} or None
    (missing file / not a repo / git failure — best-effort by design;
    enforcement never depends on this)."""
    try:
        path = Path(file_path)
        if not path.is_file() or path.stat().st_size > WRITE_BACKUP_MAX_BYTES:
            return None
        root = _git(["rev-parse", "--show-toplevel"], str(path.parent))
        if not root:
            return None
        blob = _git(["hash-object", "-w", "--", str(path)], root)
        if not blob:
            return None
        return {
            "blob": blob,
            "restore": f"git cat-file -p {blob} > {file_path}",
        }
    except OSError:
        return None


def snapshot_contains(snap: Snapshot, rel_paths: list[str]) -> bool:
    """True if every given repo-relative path is present in the snapshot.

    This is what makes the insurance VERIFIED: the gate does not allow a
    destructive retry because "a backup was taken" — it allows it because
    the files about to be destroyed provably exist inside the snapshot
    commit. Empty list → trivially true. Any doubt → False (fail closed).
    """
    if not rel_paths:
        return True
    listing = _git(["ls-tree", "-r", "--name-only", snap.commit], snap.repo_root)
    if listing is None:
        return False
    present = set(listing.splitlines())
    return all(p in present for p in rel_paths)


def list_snapshots(tail: int = 0) -> list[dict]:
    """Recorded snapshots, oldest first. tail>0 limits to the last N."""
    path = _snapshots_path()
    if not path.exists():
        return []
    records = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    except OSError:
        return []
    return records[-tail:] if tail > 0 else records
