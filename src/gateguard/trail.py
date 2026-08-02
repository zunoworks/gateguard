"""Audit trail — chain verification and the flight-recorder report.

v0.7.0. `gateguard audit` reads the hash-chained gate log and renders
the session as a flight recorder would: what the AI observed, what the
gate decided, what evidence justified each pass, and — for insured
destructive commands — the certificate binding blast radius to a
verified snapshot and a one-line rollback.

Verification mirrors the appending rule in log.py exactly: a record's
`prev` must equal the previous chained record's `h` (GENESIS after a
legacy record or at the start), and `h` must recompute from the
record's own canonical body. Any edit or deletion breaks every hash
downstream.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import log as _log


@dataclass
class ChainReport:
    ok: bool
    total: int = 0
    chained: int = 0
    legacy: int = 0
    first_break_line: int = 0  # 1-based; 0 = no break
    reason: str = ""
    head: str = ""  # h of the last chained record
    hashes: list[str] = field(default_factory=list)  # chained h's, in order

    def describe(self) -> str:
        if self.total == 0:
            return "chain: empty log"
        status = "VERIFIED" if self.ok else f"BROKEN at line {self.first_break_line} ({self.reason})"
        legacy = f", {self.legacy} legacy (pre-chain)" if self.legacy else ""
        return f"chain: {status} — {self.chained} chained record(s){legacy}"


def _read_records(path: Path) -> list[tuple[int, dict | None]]:
    """(1-based line number, parsed record or None) per non-blank line."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for i, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
            out.append((i, rec if isinstance(rec, dict) else None))
        except json.JSONDecodeError:
            out.append((i, None))
    return out


def verify_chain(path: Path | None = None) -> ChainReport:
    path = path or _log.GATE_LOG_PATH
    if not path.exists():
        return ChainReport(ok=True, total=0)

    report = ChainReport(ok=True)
    expected_prev = _log.GENESIS
    for line_no, rec in _read_records(path):
        report.total += 1
        if rec is None:
            return ChainReport(
                ok=False, total=report.total, chained=report.chained,
                legacy=report.legacy, first_break_line=line_no,
                reason="unparseable record",
            )
        h = rec.get("h")
        if not isinstance(h, str) or not h:
            # Legacy record — chain restarts after it (matches log.py).
            report.legacy += 1
            expected_prev = _log.GENESIS
            continue
        if rec.get("prev") != expected_prev:
            return ChainReport(
                ok=False, total=report.total, chained=report.chained,
                legacy=report.legacy, first_break_line=line_no,
                reason="prev-hash mismatch (record inserted/removed/reordered)",
            )
        if _log.record_hash(rec) != h:
            return ChainReport(
                ok=False, total=report.total, chained=report.chained,
                legacy=report.legacy, first_break_line=line_no,
                reason="content hash mismatch (record edited)",
            )
        report.chained += 1
        report.hashes.append(h)
        report.head = h
        expected_prev = h
    return report


# ---------- anchors ----------

def verify_anchors(chain: ChainReport, cwd: str | None = None) -> tuple[int, list[str]]:
    """Check external anchors against the chain: (verified, problems).

    An anchor pins "after N records the chain head was H" outside the
    log file — as a git object under refs/gateguard/anchors/ (created by
    `gateguard anchor`, pushable to a remote). The hash chain alone
    detects line edits but not a wholesale rewrite (no secret is
    involved); an anchor the rewriter cannot reach turns full
    regeneration into a detectable mismatch. No repo or no anchors →
    (0, []): absence of anchors is not an error, just weaker custody.
    """
    import os

    from .snapshot import _git

    workdir = cwd or os.getcwd()
    root = _git(["rev-parse", "--show-toplevel"], workdir)
    if not root:
        return 0, []
    listing = _git(
        ["for-each-ref", "--format=%(refname) %(objectname)", "refs/gateguard/anchors/"],
        root,
    )
    if not listing:
        return 0, []

    verified = 0
    problems: list[str] = []
    for line in listing.splitlines():
        try:
            ref, obj = line.split()
        except ValueError:
            continue
        blob = _git(["cat-file", "blob", obj], root)
        if blob is None:
            problems.append(f"{ref}: unreadable anchor object")
            continue
        try:
            payload = json.loads(blob)
            head = str(payload["head"])
            count = int(payload["records"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            problems.append(f"{ref}: malformed anchor payload")
            continue
        if count <= len(chain.hashes) and count > 0 and chain.hashes[count - 1] == head:
            verified += 1
        else:
            problems.append(
                f"{ref}: log does not contain anchored head {head[:12]}… at "
                f"position {count} — the trail was rewritten or truncated"
            )
    return verified, problems


# ---------- report ----------

@dataclass
class _SessionBlock:
    session: str
    cwd: str = ""
    records: list[dict] = field(default_factory=list)


def load_trail(
    path: Path | None = None,
    session: str | None = None,
    tail: int = 0,
) -> list[dict]:
    path = path or _log.GATE_LOG_PATH
    if not path.exists():
        return []
    records = [rec for _, rec in _read_records(path) if rec is not None]
    if session:
        records = [r for r in records if r.get("session", "") == session]
    return records[-tail:] if tail > 0 else records


def _fmt_ts(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "?"


def _annotate(rec: dict) -> str:
    """The right-hand context column: evidence links, certificates, blast."""
    extra = rec.get("extra") or {}
    bits = []
    cert = extra.get("certificate")
    if isinstance(cert, dict):
        bits.append(
            f"INSURED snapshot={cert.get('snapshot_id', '?')} "
            f"verified={cert.get('verified')} rollback: {cert.get('rollback', '?')}"
        )
    blast = extra.get("blast")
    if isinstance(blast, dict):
        unbacked = blast.get("unbacked_count", 0)
        bits.append(
            f"blast: {blast.get('file_count', '?')} files"
            + (f", {unbacked} unbacked" if unbacked else "")
        )
    evidence = extra.get("evidence")
    if isinstance(evidence, list) and evidence:
        shown = "; ".join(
            f"{e.get('kind', '?')} {e.get('target') or e.get('pattern', '')}".strip()
            for e in evidence[:3]
        )
        more = f" (+{len(evidence) - 3})" if len(evidence) > 3 else ""
        bits.append(f"justified by: {shown}{more}")
    reason = extra.get("reason")
    if isinstance(reason, str) and reason:
        bits.append(reason)
    return "  [" + " | ".join(bits) + "]" if bits else ""


def _group_sessions(records: list[dict]) -> list[_SessionBlock]:
    blocks: list[_SessionBlock] = []
    index: dict[str, _SessionBlock] = {}
    for rec in records:
        sid = str(rec.get("session", "") or "unknown")
        block = index.get(sid)
        if block is None:
            block = _SessionBlock(session=sid, cwd=str(rec.get("cwd", "") or ""))
            index[sid] = block
            blocks.append(block)
        block.records.append(rec)
    return blocks


def render_report(
    records: list[dict],
    chain: ChainReport,
    fmt: str = "text",
) -> str:
    if fmt == "jsonl":
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in records)

    md = fmt == "md"
    lines: list[str] = []
    title = "GateGuard audit trail"
    lines.append(f"# {title}" if md else title)
    lines.append(("**" if md else "") + chain.describe() + ("**" if md else ""))
    if not records:
        lines.append("(no records)")
        return "\n".join(lines)

    for block in _group_sessions(records):
        lines.append("")
        header = f"Session {block.session}" + (f" — {block.cwd}" if block.cwd else "")
        lines.append(f"## {header}" if md else header)
        if md:
            lines.append("")
            lines.append("```")
        for rec in block.records:
            action = str(rec.get("action", "?"))
            marker = {"deny": "DENY ", "allow": "allow", "observe": "  obs"}.get(
                action, action[:5].ljust(5)
            )
            lines.append(
                f"{_fmt_ts(rec.get('ts', 0))}  {marker}  "
                f"{str(rec.get('tool', '?')):8} {str(rec.get('gate', '?')):22} "
                f"{str(rec.get('summary', ''))[:120]}{_annotate(rec)}"
            )
        if md:
            lines.append("```")
    return "\n".join(lines)
