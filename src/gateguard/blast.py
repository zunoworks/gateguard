"""Blast-radius reconnaissance — the gate investigates the destruction.

v0.7.0. The fact-forcing gate demands that the AI list what a
destructive command will destroy — and then has no way to know whether
the list is honest or complete. This module closes that gap the same
way the recognition audit closed the investigation gap: by observing
instead of trusting. Before the gate speaks, it measures the blast
radius itself and puts the numbers in the deny message.

The one number that matters most is `unbacked`: files in the blast
radius that are untracked or modified relative to HEAD — state that
exists NOWHERE except the working tree. That is exactly what the 2026
incident reports lost. Tracked-and-clean files are recoverable from
git history with or without GateGuard; unbacked files survive only if
the pre-destruction snapshot provably contains them.

Recon is best-effort and says so. Three command classes:

  paths     — `rm ...`: targets parsed from the command line, globs
              expanded, walked with caps.
  worktree  — `git reset --hard` / `git checkout --` / `git clean -f`:
              the blast radius IS the dirty state (`git status
              --porcelain`); every hit is unbacked by definition.
  opaque    — drop table / truncate / dd / force-push: no filesystem
              recon possible; the report is honest about it.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path

GIT_TIMEOUT_SEC = 5.0

# Walk caps: recon runs inside a hook with a hard timeout; a bounded
# answer plus `truncated: true` beats a precise answer that never
# arrives.
MAX_WALK_ENTRIES = 5000

_WORKTREE_DESTRUCTIVE = re.compile(
    r"\bgit\s+(reset\s+--hard|checkout\s+--|clean\s+-f)", re.IGNORECASE
)
_RM_SEGMENT = re.compile(r"^\s*(sudo\s+)?rm\b", re.IGNORECASE)


@dataclass
class BlastReport:
    kind: str  # "paths" | "worktree" | "opaque"
    targets: list[str] = field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0
    # Untracked/modified relative to HEAD — recoverable ONLY from the
    # snapshot. Repo-root-relative paths.
    unbacked: list[str] = field(default_factory=list)
    # Targets a git snapshot cannot cover. Uninsurable.
    outside_repo: list[str] = field(default_factory=list)
    truncated: bool = False
    note: str = ""

    def summary(self) -> dict:
        """Compact form for log records and deny-message extras."""
        return {
            "kind": self.kind,
            "targets": self.targets[:10],
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "unbacked": self.unbacked[:20],
            "unbacked_count": len(self.unbacked),
            "outside_repo": self.outside_repo[:10],
            "truncated": self.truncated,
            "note": self.note,
        }


def _git_lines(args: list[str], cwd: str) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def _repo_root(cwd: str) -> str | None:
    lines = _git_lines(["rev-parse", "--show-toplevel"], cwd)
    return lines[0] if lines else None


def _porcelain_paths(cwd: str, targets: list[str] | None = None) -> list[str] | None:
    """Repo-relative paths with local-only state (untracked or modified)."""
    args = ["status", "--porcelain", "--untracked-files=all"]
    if targets:
        args += ["--", *targets]
    lines = _git_lines(args, cwd)
    if lines is None:
        return None
    out = []
    for ln in lines:
        # Format: "XY path" / "XY old -> new" (renames keep the new side).
        path = ln[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        out.append(path.strip('"'))
    return out


def _rm_targets(command: str) -> list[str]:
    """Paths named by rm segments of a (possibly compound) command."""
    targets: list[str] = []
    for segment in re.split(r"&&|\|\||;|\|", command):
        if not _RM_SEGMENT.match(segment):
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        for tok in tokens:
            if tok in ("sudo", "rm") or tok.startswith("-"):
                continue
            targets.append(os.path.expanduser(tok))
    return targets


def _walk_size(path: Path, budget: int) -> tuple[int, int, bool]:
    """(files, bytes, truncated) for a file or directory, capped."""
    if path.is_file() or path.is_symlink():
        try:
            return 1, path.lstat().st_size, False
        except OSError:
            return 1, 0, False
    files = 0
    total = 0
    for root, dirs, names in os.walk(path, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            files += 1
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                pass
            if files >= budget:
                return files, total, True
    return files, total, False


def analyze_blast(command: str, cwd: str | None = None) -> BlastReport:
    """Measure what `command` would destroy. Never raises."""
    workdir = cwd or os.getcwd()
    try:
        return _analyze(command, workdir)
    except Exception:
        return BlastReport(kind="opaque", note="recon failed; treating blast radius as unknown")


def _analyze(command: str, workdir: str) -> BlastReport:
    root = _repo_root(workdir)

    if _WORKTREE_DESTRUCTIVE.search(command):
        if root is None:
            return BlastReport(
                kind="opaque",
                note="git worktree command outside a git repo; recon unavailable",
            )
        dirty = _porcelain_paths(root)
        if dirty is None:
            return BlastReport(kind="opaque", note="git status failed; recon unavailable")
        total = 0
        for rel in dirty:
            try:
                total += (Path(root) / rel).lstat().st_size
            except OSError:
                pass
        return BlastReport(
            kind="worktree",
            targets=[command.strip()[:120]],
            file_count=len(dirty),
            total_bytes=total,
            unbacked=sorted(dirty),
            note="blast radius = uncommitted local state; every file listed is "
            "unrecoverable from git history",
        )

    raw_targets = _rm_targets(command)
    if not raw_targets:
        return BlastReport(
            kind="opaque",
            note="no filesystem recon for this command class (database/device/remote); "
            "snapshot covers the git worktree only",
        )

    report = BlastReport(kind="paths")
    budget = MAX_WALK_ENTRIES
    resolved: list[Path] = []
    for target in raw_targets:
        matches = glob(target) if any(c in target for c in "*?[") else [target]
        if not matches:
            report.targets.append(target + " (no match)")
            continue
        for m in matches:
            p = Path(m)
            if not p.is_absolute():
                p = Path(workdir) / p
            report.targets.append(str(p))
            if not p.exists() and not p.is_symlink():
                continue
            resolved.append(p)
            files, size, trunc = _walk_size(p, budget)
            report.file_count += files
            report.total_bytes += size
            report.truncated = report.truncated or trunc
            budget = max(1, budget - files)

    if root is None:
        report.outside_repo = [str(p) for p in resolved]
        report.note = "not inside a git repo — nothing here is insurable by snapshot"
        return report

    root_path = Path(root)
    inside: list[str] = []
    for p in resolved:
        try:
            rel = p.resolve().relative_to(root_path.resolve())
            inside.append(str(rel))
        except (ValueError, OSError):
            report.outside_repo.append(str(p))

    if inside:
        unbacked = _porcelain_paths(root, inside)
        if unbacked is None:
            report.note = "git status failed; unbacked detection unavailable"
        else:
            report.unbacked = sorted(unbacked)
    return report


def format_blast(report: BlastReport) -> str:
    """Human block appended to the destructive deny message."""
    lines = ["", "GateGuard measured the blast radius itself:"]
    if report.kind == "opaque":
        lines.append(f"- {report.note}")
        return "\n".join(lines)
    shown = ", ".join(report.targets[:5]) or "(none)"
    more = f" (+{len(report.targets) - 5} more)" if len(report.targets) > 5 else ""
    lines.append(f"- Targets: {shown}{more}")
    approx = "≥" if report.truncated else ""
    lines.append(
        f"- Contents: {approx}{report.file_count} files, "
        f"{approx}{_human_bytes(report.total_bytes)}"
    )
    if report.unbacked:
        head = ", ".join(report.unbacked[:5])
        more = f" (+{len(report.unbacked) - 5} more)" if len(report.unbacked) > 5 else ""
        lines.append(
            f"- ⚠ {len(report.unbacked)} file(s) exist ONLY in the working tree "
            f"(untracked/modified — no git history has them): {head}{more}"
        )
    else:
        lines.append("- No untracked/modified files in the blast radius.")
    if report.outside_repo:
        head = ", ".join(report.outside_repo[:5])
        lines.append(
            f"- ⚠ Outside the git worktree (a snapshot cannot cover these): {head}"
        )
    if report.note:
        lines.append(f"- Note: {report.note}")
    lines.append(
        "\nReconcile your fact list with these measurements before retrying."
    )
    return "\n".join(lines)


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(n)} B"
