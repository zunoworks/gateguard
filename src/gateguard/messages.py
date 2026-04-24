"""Gate message templates.

Users can override any of these via `.gateguard.yml` → `messages` dict.
Keys: edit, write, bash_destructive, bash_routine, bughunt

Message design (v0.5.0):
  - First line: what happened and why it paused (user-facing translation)
  - Second block: "AI, before retrying:" — explicit addressee, plain verbs
  - Plain words over jargon: "search" not "Grep", "files that use this"
    not "import/require", "paused" not "denied"
"""

from __future__ import annotations


DEFAULT_EDIT = (
    "🛡️ GateGuard paused this edit — confirm the impact first.\n\n"
    "The AI tried to edit {file_path} without first checking what it affects.\n\n"
    "AI, before retrying:\n"
    "1. Quote the user's current instruction verbatim (to confirm the change is in scope).\n"
    "2. Search the codebase for every file that uses this one, and list them.\n"
    "3. If existing code conflicts with the user's instruction, state the conflict "
    "explicitly. When in conflict, the user's instruction wins.\n"
    "4. If this file reads/writes data files, check one real record and confirm "
    "field names, structure, and date format align (use redacted values).\n\n"
    "Present the findings, then retry the same operation.\n\n"
    "Note: parallel edits to the same file within 2 seconds are auto-blocked. "
    "After presenting facts, retry one at a time."
)

DEFAULT_WRITE = (
    "🛡️ GateGuard paused this new file — confirm it's needed first.\n\n"
    "The AI tried to create {file_path} without first checking for duplicates.\n\n"
    "AI, before retrying:\n"
    "1. Quote the user's current instruction verbatim.\n"
    "2. Search the codebase for any existing file that already provides this.\n"
    "3. If existing code conflicts with the user's instruction, state the conflict "
    "explicitly. When in conflict, the user's instruction wins.\n"
    "4. If this file will read/write data files, check one real record and confirm "
    "field names, structure, and date format align (use redacted values).\n\n"
    "Present the findings, then retry the same operation.\n\n"
    "Note: parallel creates of the same file within 2 seconds are auto-blocked. "
    "After presenting facts, retry one at a time."
)

DEFAULT_BASH_DESTRUCTIVE = (
    "🛡️ GateGuard paused this command — it may be hard to undo.\n\n"
    "The AI tried to run a destructive command (delete / overwrite / force-push / etc.).\n\n"
    "AI, before retrying:\n"
    "1. List the files or data this command will modify or delete.\n"
    "2. Write a one-line rollback procedure in case you need to undo.\n"
    "3. Quote the user's current instruction verbatim.\n\n"
    "Present the findings, then retry the same operation."
)

DEFAULT_BASH_ROUTINE = (
    "🛡️ GateGuard paused this command — confirm scope first.\n\n"
    "The AI tried to run a shell command. Confirm alignment with the user's "
    "current instruction before it executes.\n\n"
    "AI, before retrying:\n"
    "Quote the user's current instruction verbatim, then retry the operation."
)

DEFAULT_BUGHUNT = (
    "🛡️ GateGuard paused this — tests haven't been run after recent edits.\n\n"
    "The AI has made 3+ Edit/Write operations with no test, build, or benchmark "
    "run since. Before the next operation, verify nothing is broken.\n\n"
    "AI, before retrying:\n"
    "1. Run the relevant tests (pytest / npm test / cargo test / etc.).\n"
    "2. Verify the build still succeeds (if applicable).\n"
    "3. Exercise the changed code on real input.\n"
    "4. Check edge cases (empty, huge, concurrent, timezone, size bloat).\n\n"
    "Present the verification result in the same turn, then retry.\n"
    "Bug-hunting should be proactive, not user-triggered.\n\n"
    "To temporarily disable: set env var GATEGUARD_BUGHUNT_DISABLED=1"
)


def _sanitize_path(file_path: str) -> str:
    """Strip newlines and control characters to prevent message injection."""
    return file_path.replace("\n", " ").replace("\r", " ").strip()[:500]


def edit_gate_msg(file_path: str, overrides: dict[str, str] | None = None) -> str:
    template = (overrides or {}).get("edit", DEFAULT_EDIT)
    return template.replace("{file_path}", _sanitize_path(file_path))


def write_gate_msg(file_path: str, overrides: dict[str, str] | None = None) -> str:
    template = (overrides or {}).get("write", DEFAULT_WRITE)
    return template.replace("{file_path}", _sanitize_path(file_path))


def bash_destructive_gate(overrides: dict[str, str] | None = None) -> str:
    return (overrides or {}).get("bash_destructive", DEFAULT_BASH_DESTRUCTIVE)


def bash_routine_gate(overrides: dict[str, str] | None = None) -> str:
    return (overrides or {}).get("bash_routine", DEFAULT_BASH_ROUTINE)


def bughunt_gate_msg(overrides: dict[str, str] | None = None) -> str:
    return (overrides or {}).get("bughunt", DEFAULT_BUGHUNT)
