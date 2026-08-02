"""Audit trail — hash chain integrity, tamper detection, report rendering."""

from __future__ import annotations

import json
from pathlib import Path

from gateguard import log as log_mod
from gateguard.log import log_event
from gateguard.trail import load_trail, render_report, verify_chain


def _append_events(n: int) -> None:
    for i in range(n):
        log_event("Edit", {"file_path": f"/tmp/f{i}.py", "old_string": "x"}, "passed", "allow")


def test_records_carry_session_and_chain_fields() -> None:
    _append_events(1)
    rec = json.loads(log_mod.GATE_LOG_PATH.read_text(encoding="utf-8").strip())
    assert rec["session"]
    assert rec["cwd"]
    assert rec["prev"] == log_mod.GENESIS
    assert rec["h"]


def test_intact_chain_verifies() -> None:
    _append_events(5)
    report = verify_chain()
    assert report.ok
    assert report.chained == 5
    assert report.legacy == 0


def test_edited_record_breaks_chain() -> None:
    _append_events(3)
    lines = log_mod.GATE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["summary"] = "doctored"
    lines[1] = json.dumps(rec, ensure_ascii=False)
    log_mod.GATE_LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = verify_chain()
    assert not report.ok
    assert report.first_break_line == 2
    assert "edited" in report.reason


def test_deleted_record_breaks_chain() -> None:
    _append_events(3)
    lines = log_mod.GATE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    del lines[1]
    log_mod.GATE_LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = verify_chain()
    assert not report.ok
    assert "inserted/removed/reordered" in report.reason


def test_legacy_records_restart_the_chain() -> None:
    # Pre-v0.7.0 record: no hash fields.
    legacy = {"ts": 1.0, "tool": "Edit", "gate": "passed", "action": "allow", "summary": ""}
    log_mod.GATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_mod.GATE_LOG_PATH.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    _append_events(2)

    report = verify_chain()
    assert report.ok
    assert report.legacy == 1
    assert report.chained == 2


def test_long_record_does_not_fork_the_chain() -> None:
    """Regression: an oversized record beyond the old 8KB tail window
    made _last_hash return GENESIS, forking the chain — an honest log
    then verified as tampered."""
    from gateguard.log import GENESIS, record_hash

    big = {
        "ts": 1.0, "session": "s", "cwd": "/", "tool": "Bash",
        "gate": "fact_force_destructive", "action": "deny",
        "summary": "x" * 30000, "prev": GENESIS,
    }
    big["h"] = record_hash(big)
    log_mod.GATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_mod.GATE_LOG_PATH.write_text(
        json.dumps(big, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    _append_events(1)
    report = verify_chain()
    assert report.ok
    assert report.chained == 2


def test_empty_log_verifies() -> None:
    report = verify_chain()
    assert report.ok
    assert report.total == 0


def test_report_renders_certificate_and_evidence() -> None:
    log_event(
        "Read", {"file_path": "/proj/src/auth.py"}, "evidence", "observe",
    )
    log_event(
        "Edit", {"file_path": "/proj/src/auth.py", "old_string": "x"},
        "evidence_pass", "allow",
        extra={"evidence": [{"kind": "grep", "target": "/proj/src", "pattern": "auth", "ts": 1.0}]},
    )
    log_event(
        "Bash", {"command": "rm -rf build"}, "destructive_insured", "allow",
        extra={
            "certificate": {
                "snapshot_id": "20260802T000000Z-abcd1234",
                "verified": True,
                "rollback": "git restore --source=abcd1234 --worktree -- .",
            },
            "blast": {"file_count": 12, "unbacked_count": 2},
        },
    )

    records = load_trail()
    text = render_report(records, verify_chain(), fmt="text")
    assert "justified by: grep" in text
    assert "INSURED snapshot=20260802T000000Z-abcd1234" in text
    assert "blast: 12 files, 2 unbacked" in text
    assert "chain: VERIFIED" in text

    md = render_report(records, verify_chain(), fmt="md")
    assert md.startswith("# GateGuard audit trail")

    jsonl = render_report(records, verify_chain(), fmt="jsonl")
    assert len(jsonl.splitlines()) == 3


def test_load_trail_filters_by_session_and_tail() -> None:
    _append_events(4)
    all_records = load_trail()
    sid = all_records[0]["session"]
    assert len(load_trail(session=sid)) == 4
    assert len(load_trail(session="nope")) == 0
    assert len(load_trail(tail=2)) == 2
