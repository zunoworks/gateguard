"""Gate message templates.

Users can override any of these via `.gateguard.yml` → `messages` dict.
Keys: edit, write, bash_destructive, bash_routine
"""

from __future__ import annotations


DEFAULT_EDIT = (
    "[GateGuard — fact-forcing gate]\n\n"
    "Before editing {file_path}, present the following facts:\n\n"
    "1. List every file that imports/requires this file (use Grep).\n"
    "2. List the public functions and classes your change will affect.\n"
    "3. If this file reads/writes data files, show field names, structure, and date format "
    "(use redacted or synthetic values, not raw production data).\n"
    "4. Quote the user's current instruction verbatim "
    "(to confirm the change is in scope).\n\n"
    "Present the facts, then retry the same operation."
)

DEFAULT_WRITE = (
    "[GateGuard — fact-forcing gate]\n\n"
    "Before creating {file_path}, present the following facts:\n\n"
    "1. Name the call sites (file and line) where this file will be used.\n"
    "2. Use Glob to confirm no existing file already provides this.\n"
    "3. If this file will read/write data files, show field names, structure, and date format "
    "(use redacted or synthetic values, not raw production data).\n"
    "4. Quote the user's current instruction verbatim.\n\n"
    "Present the facts, then retry the same operation."
)

DEFAULT_BASH_DESTRUCTIVE = (
    "[GateGuard — fact-forcing gate]\n\n"
    "A destructive command was detected. Before running it, present:\n\n"
    "1. The files or data this command will modify or delete.\n"
    "2. A one-line rollback procedure.\n"
    "3. The user's current instruction verbatim.\n\n"
    "Present the facts, then retry the same operation."
)

DEFAULT_BASH_ROUTINE = (
    "[GateGuard — fact-forcing gate]\n\n"
    "Quote the user's current instruction verbatim.\n"
    "After quoting, retry the operation."
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
