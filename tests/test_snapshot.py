"""Pre-destruction snapshots — capture, verification, non-intrusiveness."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gateguard.snapshot import (
    capture_snapshot,
    list_snapshots,
    snapshot_contains,
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_capture_includes_untracked_and_modified(repo: Path) -> None:
    (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("only here\n", encoding="utf-8")

    snap = capture_snapshot(str(repo), command="rm -rf .")
    assert snap is not None
    listing = _git(repo, "ls-tree", "-r", "--name-only", snap.commit)
    assert "tracked.txt" in listing
    assert "untracked.txt" in listing
    # The snapshotted content is the working-tree state, not HEAD's.
    blob = _git(repo, "show", f"{snap.commit}:tracked.txt")
    assert blob == "modified"


def test_capture_does_not_touch_user_state(repo: Path) -> None:
    (repo / "untracked.txt").write_text("x\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD")
    status_before = _git(repo, "status", "--porcelain")

    snap = capture_snapshot(str(repo), command="rm -rf .")
    assert snap is not None
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "status", "--porcelain") == status_before


def test_snapshot_ref_keeps_commit_reachable(repo: Path) -> None:
    snap = capture_snapshot(str(repo), command="rm -rf .")
    assert snap is not None
    refs = _git(repo, "show-ref")
    assert snap.ref in refs


def test_capture_outside_git_returns_none(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert capture_snapshot(str(plain), command="rm -rf .") is None


def test_capture_in_unborn_repo(tmp_path: Path) -> None:
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "only.txt").write_text("x\n", encoding="utf-8")
    snap = capture_snapshot(str(repo), command="rm only.txt")
    assert snap is not None
    assert "only.txt" in _git(repo, "ls-tree", "-r", "--name-only", snap.commit)


def test_snapshot_contains_verifies_paths(repo: Path) -> None:
    (repo / "untracked.txt").write_text("x\n", encoding="utf-8")
    snap = capture_snapshot(str(repo), command="rm -rf .")
    assert snap is not None
    assert snapshot_contains(snap, ["tracked.txt", "untracked.txt"])
    assert not snapshot_contains(snap, ["ghost.txt"])
    assert snapshot_contains(snap, [])


def test_snapshots_are_recorded_for_listing(repo: Path) -> None:
    snap = capture_snapshot(str(repo), command="rm -rf build")
    assert snap is not None
    records = list_snapshots()
    assert len(records) == 1
    assert records[0]["id"] == snap.id
    assert records[0]["command"] == "rm -rf build"
    assert "git restore --source=" in records[0]["rollback"]
