"""Recognition audit (v0.6.0) — evidence ledger, risk tiers, scope passes.

The fact-forcing gate (v0.1–v0.5) demands that the AI present facts, then
trusts the retry. That leaves one final piece of self-report inside
GateGuard itself: the gate cannot tell whether the investigation actually
happened. v0.6.0 removes it.

Three mechanisms, all computed from OBSERVED tool history — never from
what the AI claims:

  Evidence ledger
      The read-tracker hook (PostToolUse on Read|Grep|Glob|Bash) records
      investigation actions into session state. Before firing the
      fact-forcing gate, the hook consults the ledger: if the target was
      actually investigated within the TTL, the gate is skipped. The
      AI's own behavior — not its answer — is the passport.

  Risk tiers
      trivial   — comment/blank-line-only edits pass without ceremony.
      elevated  — signature changes (def/class/import/export) require
                  deep evidence: the dependents must have been searched.
      high      — auth / payment / migration / config / CI paths are
                  never exempted by evidence, and add an explicit
                  user-confirmation demand to the gate message.

  Scope passes
      Passing the gate (or deep evidence) grants the file's directory a
      time-limited pass. Files in a recently-verified directory that have
      been Read skip the ceremony; moving to a new directory re-gates.
      This resolves the "same confirmation over and over in long
      sessions" complaint structurally instead of by loosening gates.

All functions are pure over the state dict (bughunt.py style); I/O stays
in hook.py / read_tracker.py.
"""

from __future__ import annotations

import re
from pathlib import Path

# Ledger freshness window and physical cap (unbounded growth in state
# files is a known failure mode for hook ecosystems — keep both bounds).
EVIDENCE_TTL_SEC = 1800.0
EVIDENCE_MAX_ENTRIES = 200

# Directory pass lifetime after a gate pass / deep-evidence pass.
SCOPE_PASS_TTL_SEC = 1800.0

# Bash commands that count as investigation. The ledger records only
# these — execution policy is the PreToolUse hook's job, not the
# recorder's.
INVESTIGATIVE_BASH = re.compile(
    r"\b(grep|rg|find|fd|fdfind|tree|wc|stat|cat|head|tail"
    r"|git\s+(?:log|show|diff|blame|grep|status|ls-files))\b"
)

# High tier: matched against path segments split on [._-] so that
# substrings never misfire (tokenizer.py must not match "token").
# Deliberately narrow — generic words (config/env/token) are left out of
# the token set; risky FILENAMES are matched separately below.
HIGH_RISK_PATH_TOKENS = frozenset({
    "auth", "login", "oauth", "sso", "payment", "payments", "billing",
    "checkout", "secret", "secrets", "credential", "credentials",
    "migration", "migrations",
})
HIGH_RISK_FILENAME_RE = re.compile(
    r"^\.env(\..+)?$|^settings(\.local)?\.json$|\.plist$"
    # v0.7.0 self-protection: the guardrail's own config must never be
    # editable on evidence alone — otherwise "disable the gate" is one
    # ordinary gated edit away.
    r"|^\.gateguard\.ya?ml$",
    re.IGNORECASE,
)
HIGH_RISK_PATH_RE = re.compile(r"\.github/workflows/", re.IGNORECASE)

# Elevated tier: lines that define a public surface.
SIGNATURE_LINE_RE = re.compile(
    r"^\s*(async\s+def\s|def\s|class\s|import\s|from\s+\S+\s+import\s"
    r"|export\s|function\s|interface\s|type\s+\S+\s*=)"
)

# Trivial detection: full-line comments only. Inline markers ("#fff" in
# a color value) are deliberately NOT stripped — stripping them would
# misclassify a real change as a comment change.
COMMENT_LINE_RE = re.compile(r"^\s*(#|//|/\*|\*/|\*|<!--|--\s)")


# ---------- recording (called by read_tracker) ----------

def evidence_entry(tool_name: str, tool_input: dict, now: float):
    """Build a ledger entry for an observed tool call, or None if the
    call is not investigation."""
    if tool_name == "Read":
        fp = tool_input.get("file_path", "")
        if not fp:
            return None
        return {"kind": "read", "target": fp, "pattern": "", "ts": now}
    if tool_name == "Grep":
        return {"kind": "grep", "target": tool_input.get("path", "") or "",
                "pattern": tool_input.get("pattern", "") or "", "ts": now}
    if tool_name == "Glob":
        return {"kind": "glob", "target": tool_input.get("path", "") or "",
                "pattern": tool_input.get("pattern", "") or "", "ts": now}
    if tool_name == "Bash":
        cmd = tool_input.get("command", "") or ""
        if not cmd or not INVESTIGATIVE_BASH.search(cmd):
            return None
        return {"kind": "bash", "target": "", "pattern": cmd[:300], "ts": now}
    return None


def record_evidence(state: dict, entry: dict, now: float) -> dict:
    """Append a ledger entry, pruning stale entries and enforcing the cap."""
    raw = state.get("evidence", [])
    if not isinstance(raw, list):
        raw = []
    fresh = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        try:
            if now - float(e.get("ts", 0) or 0) < EVIDENCE_TTL_SEC:
                fresh.append(e)
        except (TypeError, ValueError):
            continue
    fresh.append(entry)
    state["evidence"] = fresh[-EVIDENCE_MAX_ENTRIES:]
    return state


# ---------- classification ----------

def _normalized_code_lines(text: str) -> list:
    """Lines with comments/blanks dropped and trailing whitespace ignored.

    Leading indentation is preserved — in Python indentation is semantic,
    so an indentation change must never classify as trivial.
    """
    return [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not COMMENT_LINE_RE.match(line)
    ]


def is_trivial_change(tool_name: str, tool_input: dict) -> bool:
    """True if an Edit touches only comments / blank lines / trailing space."""
    if tool_name != "Edit":
        return False
    old = tool_input.get("old_string", "") or ""
    new = tool_input.get("new_string", "") or ""
    if not old:
        return False
    return _normalized_code_lines(old) == _normalized_code_lines(new)


def risk_tier(tool_name: str, tool_input: dict, file_path: str) -> str:
    """'high' | 'elevated' | 'normal' by change location and content."""
    p = Path(file_path)
    tokens = set()
    for part in p.parts:
        tokens.update(t for t in re.split(r"[._\-]+", part.lower()) if t)
    if tokens & HIGH_RISK_PATH_TOKENS:
        return "high"
    if HIGH_RISK_FILENAME_RE.search(p.name):
        return "high"
    if HIGH_RISK_PATH_RE.search(file_path):
        return "high"
    if tool_name == "Edit":
        # Elevated only when a signature line itself CHANGED. old_string
        # often contains an unchanged def line purely as anchoring
        # context — that must not escalate.
        old = tool_input.get("old_string", "") or ""
        new = tool_input.get("new_string", "") or ""
        old_sigs = [ln.rstrip() for ln in old.splitlines()
                    if SIGNATURE_LINE_RE.match(ln)]
        new_sigs = [ln.rstrip() for ln in new.splitlines()
                    if SIGNATURE_LINE_RE.match(ln)]
        if old_sigs != new_sigs:
            return "elevated"
    return "normal"


# ---------- ledger consult ----------

def matching_evidence(
    file_path: str, state: dict, now: float, limit: int = 8
) -> list[dict]:
    """Fresh ledger entries that justify recognition of `file_path`.

    v0.7.0: this is also the causal link the audit trail records — when
    a pass is granted on evidence, the entries returned here go into the
    log record, so `gateguard audit` can answer "what did the AI
    actually check before this mutation". Read entries of the file
    itself are included for the trail; evidence_level() ignores them
    (reads are tracked via read_files).
    """
    entries = state.get("evidence", [])
    if not isinstance(entries, list):
        entries = []

    p = Path(file_path)
    stem = p.stem
    parent = str(p.parent)
    out: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        try:
            if now - float(e.get("ts", 0) or 0) > EVIDENCE_TTL_SEC:
                continue
        except (TypeError, ValueError):
            continue
        pattern = str(e.get("pattern", "") or "")
        target = str(e.get("target", "") or "")
        if e.get("kind") == "read":
            if target == file_path:
                out.append(e)
            continue
        if stem and (stem in pattern or stem in target):
            out.append(e)
        elif target and (parent == target
                         or parent.startswith(target.rstrip("/") + "/")):
            out.append(e)
    return out[-limit:]


def evidence_level(file_path: str, state: dict, now: float) -> str:
    """'deep' | 'touched' | 'none' — observed recognition of the target.

    deep    — the file was Read (not required for brand-new files) AND
              the ledger holds fresh surrounding investigation: a
              grep/glob/bash mentioning the file's stem, or a scan of
              its directory.
    touched — Read, but no surrounding investigation observed.
    none    — neither.
    """
    read_files = set(state.get("read_files", []))
    touched = file_path in read_files
    hit = any(
        e.get("kind") != "read"
        for e in matching_evidence(file_path, state, now, limit=EVIDENCE_MAX_ENTRIES)
    )

    p = Path(file_path)
    if hit and (touched or not p.exists()):
        return "deep"
    if touched:
        return "touched"
    return "none"


# ---------- scope passes ----------

def valid_scope_pass(file_path: str, state: dict, now: float) -> bool:
    """True if the file's directory holds an unexpired pass."""
    passes = state.get("scope_passes", {})
    if not isinstance(passes, dict):
        return False
    expiry = passes.get(str(Path(file_path).parent))
    try:
        return expiry is not None and float(expiry) > now
    except (TypeError, ValueError):
        return False


def grant_scope_pass(state: dict, file_path: str, now: float) -> dict:
    """Grant the file's directory a pass and sweep expired passes."""
    raw = state.get("scope_passes", {}) or {}
    passes = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                if float(v) > now:
                    passes[k] = v
            except (TypeError, ValueError):
                continue
    passes[str(Path(file_path).parent)] = now + SCOPE_PASS_TTL_SEC
    state["scope_passes"] = passes
    return state
