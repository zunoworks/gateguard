"""PreToolUse hook entrypoint — the fact-forcing gate.

Reads Claude Code's PreToolUse JSON payload from stdin and decides
whether to deny the action (emitting a gate message the LLM must handle
before retrying) or allow it (emitting nothing).

Gate taxonomy:
  Gate 1 — read_before_edit
      Edit on a file that hasn't been Read this session is denied.
  Gate 2 — fact_force
      First Edit/Write per file is denied with a fact-presentation prompt.
      Destructive Bash commands are denied per command with measured
      blast-radius facts; the retry passes only once a verified
      pre-destruction snapshot exists (v0.7.0 insurance — with
      insurance.snapshot_pass off, every attempt is denied as in v0.6.x).
      Routine Bash is gated once per session.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from .audit import (
    evidence_level,
    grant_scope_pass,
    is_trivial_change,
    matching_evidence,
    risk_tier,
    valid_scope_pass,
)
from .blast import analyze_blast, format_blast
from .snapshot import backup_file_blob, capture_snapshot, snapshot_contains
from .bughunt import (
    BUGHUNT_COMMANDS,
    bughunt_gate_should_fire,
    is_bughunt_command,
    is_debounced_edit,
    is_trivial_file,
    mark_gate_fired,
    record_bughunt,
    record_edit,
    update_recent_file_edit,
)
from .config import Config, load_config
from .log import log_event
from .readonly import is_readonly_bash
from .messages import (
    bash_destructive_gate,
    bash_routine_gate,
    bash_uninsured_gate,
    bughunt_gate_msg,
    edit_gate_msg,
    elevated_addendum,
    high_risk_addendum,
    insurance_promise,
    write_gate_msg,
)
from .state import load_state, update_state


# Built-in destructive command pattern. Users can extend via
# `.gateguard.yml` → destructive_bash_extra. The same pattern scans
# executed script CONTENT (v0.7.0), so each entry closes both the
# command-line and the write-a-script-then-run-it route at once.
#
# Boundary lesson (found by adversarial audit): a single trailing \b
# after the alternation silently broke every alternative ending in a
# non-word char next to a non-word char — `dd if=/dev/zero`,
# `git checkout -- file`, `git clean -fd`, and `rm -fr` (flag order!)
# all slipped through for six versions. Boundaries now live inside the
# alternatives that need them, and there is a regression test per class.
BUILTIN_DESTRUCTIVE_BASH = re.compile(
    # \b lives INSIDE each alternative (a shared prefix/suffix boundary
    # is exactly the bug class this pattern was rebuilt to kill — a
    # group-leading \b cannot precede the `>` redirect alternatives).
    #
    # rm with any flag bundle containing r/R (recursive) or f/F (force),
    # in any order or position. Plain `rm file` stays routine.
    r"(?:\brm\s+(?:-[a-z]*\s+)*-[a-z]*[rf]"
    r"|\brm\s+--(?:recursive|force)\b"
    r"|\bgit\s+reset\s+--hard\b|\bgit\s+checkout\s+--(?:\s|$)"
    # git clean with f anywhere in the flag soup (-fd, -d -f, -fdx) —
    # 2026 field reports: "banning rm doesn't stop git clean".
    r"|\bgit\s+clean\s+(?:-[a-z]*\s+)*-[a-z]*f"
    r"|\bdrop\s+table\b|\bdelete\s+from\b|\btruncate\b|\bgit\s+push\s+--force\b"
    r"|\bdd\s+if="
    # Infra teardown — the single most expensive 2026 incident class
    # (production DB + its snapshots gone via permitted terraform).
    # `plan` and interactive `apply` stay routine; destroy, state
    # surgery, and auto-approved apply pay the ceremony.
    r"|\b(?:terraform|tofu)\s+(?:destroy|state\s+rm|workspace\s+delete)"
    r"|\b(?:terraform|tofu)\s+apply\s+[^\n;|&]*-auto-approve"
    # v0.7.0: in-language deletion — `python -c "shutil.rmtree(...)"`
    # and friends were a regex-free bypass of the destructive gate.
    # rimraf needs an argument: `npm install rimraf` is not destruction.
    r"|\bshutil\s*\.\s*rmtree|\brimraf\s+[^\s;&|-]|\bfs\.rm(?:dir)?Sync"
    r"|\bfind\s+[^\n;|&]*\s-delete\b"
    # Self-protection, shell channel: deleting, truncating (>) or
    # rewriting (sed -i) the guard's own files must pay the destructive
    # ceremony — the Edit/Write route is already high-risk gated.
    r"|\brm\s+[^\n;|&]*\.gateguard|>>?\s*\S*\.gateguard"
    r"|\bsed\s+-i[^\n;|&]*\.gateguard"
    r"|\brm\s+[^\n;|&]*\.claude[/\\]settings|>>?\s*\S*\.claude[/\\]settings"
    r"|\bsed\s+-i[^\n;|&]*\.claude[/\\]settings)",
    re.IGNORECASE,
)

# Interpreters whose file argument the gate scans before execution.
# Best-effort: the first non-flag token after the interpreter (so
# `bash -e run.sh` is missed — noted honestly, not hidden), plus
# directly-executed paths (./run.sh, /tmp/run.sh). Binary files are
# skipped by a NUL sniff, not by extension guessing.
_SCRIPT_EXEC_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:source|\.|bash|sh|zsh|ksh|dash|python[0-9.]*|node|ruby|perl)"
    r"\s+([^\s;&|]+)",
    re.IGNORECASE,
)
_DIRECT_SCRIPT_RE = re.compile(r"(?:^|[;&|]\s*)((?:\.{1,2})?/[^\s;&|]+)")
MAX_SCRIPT_SCAN_BYTES = 262144


def _compile_destructive(cfg: Config) -> re.Pattern[str]:
    if not cfg.destructive_bash_extra:
        return BUILTIN_DESTRUCTIVE_BASH
    joined = "|".join(re.escape(p) for p in cfg.destructive_bash_extra)
    return re.compile(
        BUILTIN_DESTRUCTIVE_BASH.pattern + "|" + joined,
        re.IGNORECASE,
    )


def _compile_bughunt(cfg: Config) -> re.Pattern[str] | None:
    """v0.6.1 (issue #1): extend the test/build recognizer via
    ``bughunt_commands_extra`` — the Flutter/Dart case. None means
    "use the built-in recognizer"."""
    if not cfg.bughunt_commands_extra:
        return None
    joined = "|".join(re.escape(p) for p in cfg.bughunt_commands_extra)
    return re.compile(
        BUGHUNT_COMMANDS.pattern + "|" + joined,
        re.IGNORECASE,
    )


def _is_ignored(path_or_cmd: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path_or_cmd, pat) for pat in patterns)


def _deny(
    reason: str,
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    gate_type: str,
    extra: dict[str, Any] | None = None,
) -> None:
    log_event(tool_name, tool_input, gate_type, "deny", extra=extra)
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def _evidence_refs(file_path: str, state: dict, now: float) -> list[dict[str, Any]]:
    """Compact ledger entries for audit-trail linkage."""
    refs = []
    for e in matching_evidence(file_path, state, now, limit=5):
        refs.append({
            "kind": str(e.get("kind", ""))[:8],
            "target": str(e.get("target", ""))[:200],
            "pattern": str(e.get("pattern", ""))[:120],
            "ts": e.get("ts", 0),
        })
    return refs


def _handle_edit_or_write(
    tool_name: str,
    tool_input: dict[str, Any],
    cfg: Config,
) -> bool:
    """Returns True if the op was allowed AND should count toward bughunt tracking.

    Returns False for deny paths and for "ignored" paths (ops on .venv/** etc.
    are real edits but should not make the bughunt budget tick).
    """
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return False

    if _is_ignored(file_path, cfg.ignore_paths):
        log_event(tool_name, tool_input, "ignored", "allow")
        return False

    state = load_state()
    now = time.time()

    # Gate 0 (v0.4.0): Bughunt gate — opt-in via cfg.gates.bughunt_gate.
    # Fires before any other gate so the reminder arrives at the start of the
    # next batch of edits, not after one more deny cycle.
    if cfg.gates.bughunt_gate and bughunt_gate_should_fire(state, now=now):
        update_state(lambda s: mark_gate_fired(s, now))
        _deny(
            bughunt_gate_msg(cfg.messages),
            tool_name=tool_name,
            tool_input=tool_input,
            gate_type="bughunt_gate",
        )
        return False

    # Gate 1: Read-before-Edit (only applies to Edit — Write creates new files)
    if tool_name == "Edit" and cfg.gates.read_before_edit:
        read_files = set(state.get("read_files", []))
        if file_path not in read_files:
            _deny(
                f"File {file_path} has not been Read yet. "
                "Read the file before editing it.",
                tool_name=tool_name,
                tool_input=tool_input,
                gate_type="read_before_edit",
            )
            return False

    # Gate 2: Fact-forcing (first action per file)
    fact_enabled = (
        cfg.gates.fact_force_edit if tool_name == "Edit"
        else cfg.gates.fact_force_write
    )
    if not fact_enabled:
        log_event(tool_name, tool_input, "disabled", "allow")
        return True

    gated = set(state.get("gated_targets", []))
    if file_path in gated:
        # The gate already fired for this file; this is the post-ceremony
        # retry (or a later edit). v0.6.0: the completed ceremony verifies
        # the surrounding scope — grant the directory pass here.
        if cfg.audit.scope_pass and not valid_scope_pass(file_path, state, now):
            update_state(lambda s: grant_scope_pass(s, file_path, now))
        log_event(tool_name, tool_input, "passed", "allow")
        return True

    # v0.6.0 recognition audit — consult observed history before demanding
    # the ceremony. Order: tier → trivial → evidence → scope pass.
    tier = risk_tier(tool_name, tool_input, file_path)
    if tier == "high" and not cfg.audit.high_risk_guard:
        tier = "normal"

    if tier != "high" and cfg.audit.trivial_pass and is_trivial_change(tool_name, tool_input):
        # Comment/blank-line-only edit — no ceremony, and deliberately NOT
        # added to gated_targets: the next substantive edit is still gated.
        log_event(tool_name, tool_input, "trivial_pass", "allow")
        return False

    if tier != "high":
        level = evidence_level(file_path, state, now)
        if cfg.audit.evidence_pass and level == "deep":
            # Investigation was observed in the ledger — equivalent to the
            # ceremony. Approve and grant the directory pass.
            def _evidence_promote(s: dict) -> dict:
                targets = list(s.get("gated_targets", []))
                if file_path not in targets:
                    targets.append(file_path)
                s["gated_targets"] = targets
                return grant_scope_pass(s, file_path, now)

            update_state(_evidence_promote)
            # v0.7.0: record the causal link — WHICH observed
            # investigation justified this pass. `gateguard audit` shows
            # it as "justified by: ...".
            log_event(
                tool_name, tool_input, "evidence_pass", "allow",
                extra={"evidence": _evidence_refs(file_path, state, now)},
            )
            return True
        if (cfg.audit.scope_pass and tier == "normal" and level == "touched"
                and valid_scope_pass(file_path, state, now)):
            # Read file inside a recently-verified directory. elevated
            # (signature changes) is NOT exempted — dependents cross scopes.
            def _scope_promote(s: dict) -> dict:
                targets = list(s.get("gated_targets", []))
                if file_path not in targets:
                    targets.append(file_path)
                s["gated_targets"] = targets
                return grant_scope_pass(s, file_path, now)

            update_state(_scope_promote)
            log_event(tool_name, tool_input, "scope_pass", "allow")
            return True

    def _mark(s: dict) -> dict:
        targets = list(s.get("gated_targets", []))
        if file_path not in targets:
            targets.append(file_path)
        s["gated_targets"] = targets
        return s

    update_state(_mark)

    if tool_name == "Edit":
        msg = edit_gate_msg(file_path, cfg.messages)
    else:
        msg = write_gate_msg(file_path, cfg.messages)

    gate_type = "fact_force"
    if tier == "high":
        # Never exempted by evidence, session state, or scope passes.
        msg += high_risk_addendum(cfg.messages)
        gate_type = "fact_force_high"
    elif tier == "elevated":
        msg += elevated_addendum(cfg.messages)

    _deny(msg, tool_name=tool_name, tool_input=tool_input, gate_type=gate_type)
    return False


def _destructive_script(
    command: str, destructive_re: re.Pattern[str]
) -> tuple[str, str] | tuple[None, None]:
    """(path, content) of the first executed script whose content is
    destructive — the write-a-script-then-run-it bypass, closed by
    scanning what is about to run instead of trusting the command line.
    """
    candidates = [m.group(1) for m in _SCRIPT_EXEC_RE.finditer(command)]
    candidates += [m.group(1) for m in _DIRECT_SCRIPT_RE.finditer(command)]

    for cand in candidates:
        if cand.startswith("-"):
            continue
        path = Path(os.path.expanduser(cand))
        try:
            if not path.is_file() or path.stat().st_size > MAX_SCRIPT_SCAN_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:1024]:
            # Compiled binary (directly-executed paths catch /usr/bin/*
            # too) — scanning machine code for shell patterns is noise.
            continue
        content = raw.decode("utf-8", errors="replace")
        if destructive_re.search(content):
            return str(path), content
    return None, None


def _destructive_key(command: str) -> str:
    normalized = " ".join(command.split())
    return "__destructive__" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _handle_destructive(
    command: str,
    tool_input: dict[str, Any],
    cfg: Config,
    script_path: str | None = None,
    script_content: str | None = None,
) -> bool:
    """v0.7.0 insurance flow: deny once with measured facts, then allow
    the exact same command ONLY with a verified snapshot in hand.

    Fail-closed by design: every path that cannot produce verified
    insurance ends in a deny — which is exactly the v0.6.x wall. With
    insurance.snapshot_pass off, the wall is all there is.

    When the destruction lives in an executed script's CONTENT rather
    than the command line, recon runs over the script text and the deny
    names the script.
    """
    recon_source = script_content if script_content else command
    recon = analyze_blast(recon_source) if cfg.insurance.blast_recon else None
    recon_extra: dict[str, Any] | None = None
    if recon:
        recon_extra = {"blast": recon.summary()}
    if script_path:
        recon_extra = recon_extra or {}
        recon_extra["script"] = script_path

    key = _destructive_key(command)
    state = load_state()
    first_attempt = key not in set(state.get("gated_targets", []))

    if first_attempt or not cfg.insurance.snapshot_pass:
        if first_attempt:
            def _mark(s: dict) -> dict:
                targets = list(s.get("gated_targets", []))
                if key not in targets:
                    targets.append(key)
                s["gated_targets"] = targets
                return s

            update_state(_mark)
        msg = bash_destructive_gate(cfg.messages)
        if script_path:
            msg += (
                f"\n\nNote: the destructive operation is inside {script_path} — "
                "GateGuard scanned the script's content, not just the command "
                "line. Running destruction via a script does not skip the gate."
            )
        if recon:
            msg += "\n" + format_blast(recon)
        if cfg.insurance.snapshot_pass:
            msg += insurance_promise(cfg.messages)
        _deny(
            msg,
            tool_name="Bash",
            tool_input=tool_input,
            gate_type="fact_force_destructive",
            extra=recon_extra,
        )
        return False

    # Retry of a command that already paid the ceremony — secure insurance.
    if recon and recon.outside_repo:
        _deny(
            bash_uninsured_gate(
                "targets outside the git worktree cannot be covered by a "
                "snapshot: " + ", ".join(recon.outside_repo[:5]),
                cfg.messages,
            ),
            tool_name="Bash",
            tool_input=tool_input,
            gate_type="destructive_uninsured",
            extra=recon_extra,
        )
        return False

    snap = capture_snapshot(command=command)
    if snap is None:
        _deny(
            bash_uninsured_gate(
                "snapshot capture failed (not a git repository, or git "
                "unavailable/timed out)",
                cfg.messages,
            ),
            tool_name="Bash",
            tool_input=tool_input,
            gate_type="destructive_uninsured",
            extra=recon_extra,
        )
        return False

    unbacked = recon.unbacked if recon else []
    verified = snapshot_contains(snap, unbacked)
    if not verified:
        _deny(
            bash_uninsured_gate(
                "snapshot verification failed — the files at risk could not "
                "be confirmed inside the snapshot",
                cfg.messages,
            ),
            tool_name="Bash",
            tool_input=tool_input,
            gate_type="destructive_uninsured",
            extra=recon_extra,
        )
        return False

    certificate = {
        "snapshot_id": snap.id,
        "commit": snap.commit,
        "ref": snap.ref,
        "repo_root": snap.repo_root,
        "verified": True,
        "covered_unbacked": len(unbacked),
        "rollback": snap.rollback_command,
    }
    extra: dict[str, Any] = {"certificate": certificate}
    if recon:
        extra["blast"] = recon.summary()
    log_event("Bash", tool_input, "destructive_insured", "allow", extra=extra)
    return True


def _handle_bash(tool_input: dict[str, Any], cfg: Config) -> bool:
    """Returns True if the op was allowed AND should count toward bughunt tracking."""
    command = tool_input.get("command", "")
    if not command:
        return False

    if _is_ignored(command, cfg.ignore_paths):
        log_event("Bash", tool_input, "ignored", "allow")
        return False

    # v0.6.0: read-only commands (ls, cat, grep, git status, ...) observe
    # without mutating — gating them is pure friction. Destructive
    # detection is unreachable for them by definition (a command cannot
    # be both read-only and match the destructive patterns).
    if cfg.gates.readonly_bash_bypass and is_readonly_bash(command):
        log_event("Bash", tool_input, "readonly_pass", "allow")
        return True

    # Gate 0 (v0.4.0): Bughunt gate — opt-in.
    # Skip when the command itself is a bughunt run (pytest, npm test, etc.);
    # denying the clearing command would be circular.
    if cfg.gates.bughunt_gate and not is_bughunt_command(command, _compile_bughunt(cfg)):
        state = load_state()
        now = time.time()
        if bughunt_gate_should_fire(state, now=now):
            update_state(lambda s: mark_gate_fired(s, now))
            _deny(
                bughunt_gate_msg(cfg.messages),
                tool_name="Bash",
                tool_input=tool_input,
                gate_type="bughunt_gate",
            )
            return False

    destructive_re = _compile_destructive(cfg)
    if cfg.gates.fact_force_bash_destructive:
        if destructive_re.search(command):
            return _handle_destructive(command, tool_input, cfg)
        # The command line looks clean — scan what it EXECUTES. This
        # closes the classic bypass: write the rm into a script, run
        # the script.
        script_path, script_content = _destructive_script(command, destructive_re)
        if script_path is not None:
            return _handle_destructive(
                command, tool_input, cfg,
                script_path=script_path, script_content=script_content,
            )

    if not cfg.gates.fact_force_bash_routine:
        log_event("Bash", tool_input, "disabled", "allow")
        return True

    state = load_state()
    gated = set(state.get("gated_targets", []))
    if "__bash_session__" in gated:
        log_event("Bash", tool_input, "passed", "allow")
        return True

    def _mark(s: dict) -> dict:
        targets = list(s.get("gated_targets", []))
        if "__bash_session__" not in targets:
            targets.append("__bash_session__")
        s["gated_targets"] = targets
        return s

    update_state(_mark)
    _deny(
        bash_routine_gate(cfg.messages),
        tool_name="Bash",
        tool_input=tool_input,
        gate_type="fact_force_routine",
    )
    return False


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}

    cfg = load_config()
    if not cfg.enabled:
        log_event(tool_name, tool_input, "disabled_global", "allow")
        return

    if tool_name in ("Edit", "Write"):
        allowed = _handle_edit_or_write(tool_name, tool_input, cfg)
        if allowed and tool_name == "Write" and cfg.insurance.write_backup:
            # This Write is about to replace the file's entire content;
            # if the old content is uncommitted it exists nowhere else.
            # Stash it as a git blob and record the restore command.
            fp = (tool_input or {}).get("file_path", "")
            backup = backup_file_blob(fp) if fp else None
            if backup:
                log_event(
                    "Write", tool_input, "write_backup", "observe",
                    extra={"backup": backup},
                )
        if allowed and cfg.gates.bughunt_gate:
            file_path = (tool_input or {}).get("file_path", "")
            # v0.4.1: docs/plaintext edits never count; re-edits to the same
            # file within BUGHUNT_DEBOUNCE_SEC don't add to the budget either.
            if file_path and not is_trivial_file(file_path):
                now = time.time()

                def _update(s: dict) -> dict:
                    if not is_debounced_edit(s, file_path, now=now):
                        record_edit(s, now)
                    update_recent_file_edit(s, file_path, now)
                    return s

                update_state(_update)
        return

    if tool_name == "Bash":
        allowed = _handle_bash(tool_input, cfg)
        if allowed and cfg.gates.bughunt_gate:
            command = (tool_input or {}).get("command", "")
            if is_bughunt_command(command, _compile_bughunt(cfg)):
                update_state(lambda s: record_bughunt(s, time.time()))
        return

    # Unknown / untracked tool — allow.
    log_event(tool_name, tool_input, "untracked", "allow")


if __name__ == "__main__":
    main()
