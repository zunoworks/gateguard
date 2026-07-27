"""Read-only Bash detection — commands that observe, never mutate.

Read-only commands (ls, cat, grep, git status, ...) bypass the routine
Bash gate entirely: forcing an instruction-quote before `git status` is
pure friction and was the top over-blocking complaint against fact-forcing
gates in the wild. The destructive gate is unaffected — this module only
ever says "this cannot write", and says "not read-only" whenever unsure.

Hardening notes (ported from the battle-tested internal lineage):
  - Single pipes are allowed when EVERY stage is independently read-only
    (`grep x f | head -20` is exactly as read-only as `grep x f`), but
    `||` stays disqualifying — its fallback branch can run anything.
  - Newlines/carriage returns disqualify immediately: in a shell a
    newline is a command separator, so `ls\\nrm -rf /` must never ride
    a read-only verdict.
  - `;` `&` `<` `>` `$()` backticks and sudo disqualify immediately —
    they can smuggle a write a same-verb pipe cannot.
  - git global options (`git -C x log`, `git --no-pager diff`) are
    skipped before classifying the subcommand.
  - find/fd are read-only only WITHOUT their execute/delete flags
    (`find -delete`, `fd -x rm` run arbitrary commands per match).
"""

from __future__ import annotations

import shlex

# Disqualifiers for the whole command line. Lone `|` is handled by
# per-stage checks instead.
import re

COMPOUND_SHELL_NO_PIPE = re.compile(r"[;&<>\n\r]|\$\(|`|^\s*sudo\b|\|\|", re.IGNORECASE)

READONLY_COMMANDS = {
    "ls", "pwd", "whoami", "which", "type", "cat", "head", "tail", "wc",
    "file", "stat", "echo", "printf", "date", "id", "uname", "hostname",
    "tree", "grep", "rg",
    # fd is NOT here — it executes arbitrary commands via -x/--exec and
    # goes through the dedicated flag check below.
}
READONLY_GIT_SUBCOMMANDS = {"status", "log", "diff", "show", "branch", "remote", "ls-files"}
# git global options that may legally appear BEFORE the subcommand
# (`git -C <path> log` must classify by "log", not by "-C").
GIT_GLOBAL_OPTS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
GIT_GLOBAL_OPTS_BARE = {"--no-pager", "-P", "--paginate", "-p", "--no-optional-locks"}
DANGEROUS_FIND_ACTIONS = {
    "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls",
}
DANGEROUS_FD_ACTIONS = {"-x", "-X", "--exec", "--exec-batch"}


def is_readonly_bash(command: str) -> bool:
    """True if the command is read-only: a single invocation, or a
    pipeline (`cmd1 | cmd2 | ...`) where every stage is independently
    read-only."""
    if COMPOUND_SHELL_NO_PIPE.search(command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False

    # Split on bare `|` tokens. A `|` inside quotes (grep "a|b") comes
    # back from shlex fused into its surrounding token, never alone —
    # so this only catches real shell pipes.
    stages = [[]]
    for tok in tokens:
        if tok == "|":
            if not stages[-1]:
                return False  # leading/double pipe — malformed, don't guess
            stages.append([])
        else:
            stages[-1].append(tok)
    if not stages[-1]:
        return False  # trailing pipe

    return all(_is_readonly_single_argv(argv) for argv in stages)


def _is_readonly_single_argv(argv: list) -> bool:
    """True if a single (already-tokenized) invocation is read-only."""
    if not argv:
        return False

    executable = argv[0]
    if executable in READONLY_COMMANDS:
        return True

    if executable == "env":
        # Bare env prints the environment; with any utility or
        # assignment it can execute arbitrary commands.
        return argv in (["env"], ["env", "-0"], ["env", "--null"])

    if executable == "find":
        return not any(arg in DANGEROUS_FIND_ACTIONS for arg in argv[1:])

    if executable in ("fd", "fdfind"):
        return not any(arg in DANGEROUS_FD_ACTIONS for arg in argv[1:])

    if executable != "git" or len(argv) < 2:
        return False

    # Skip global options before the subcommand; unknown dash-options
    # classify conservatively as non-read-only.
    idx = 1
    subcommand = ""
    while idx < len(argv):
        arg = argv[idx]
        if arg in GIT_GLOBAL_OPTS_WITH_ARG:
            idx += 2
            continue
        if "=" in arg and arg.split("=", 1)[0] in GIT_GLOBAL_OPTS_WITH_ARG:
            idx += 1
            continue
        if arg in GIT_GLOBAL_OPTS_BARE:
            idx += 1
            continue
        if arg.startswith("-"):
            return False
        subcommand = arg
        break
    if not subcommand:
        return False

    rest = argv[idx + 1:]
    if subcommand == "config":
        return len(rest) >= 1 and rest[0] in {"--get", "--get-regexp"}
    if subcommand not in READONLY_GIT_SUBCOMMANDS:
        return False
    if subcommand == "diff":
        return not any(arg == "--ext-diff" or arg.startswith("--output") for arg in rest)
    return True
