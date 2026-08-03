"""Hook entrypoint smoke tests — deny/allow based on state."""

from __future__ import annotations

import io
import json

import pytest

from gateguard import hook
from gateguard.state import update_state


def _invoke(monkeypatch: pytest.MonkeyPatch, payload: dict) -> dict | None:
    """Run hook.main() with a fake stdin/stdout and return the emitted JSON (or None)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    hook.main()
    raw = buf.getvalue()
    return json.loads(raw) if raw.strip() else None


def test_edit_without_read_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _invoke(
        monkeypatch,
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/foo.py", "old_string": "x"}},
    )
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "not been Read" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_first_edit_after_read_is_fact_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    update_state(lambda s: {**s, "read_files": ["/tmp/foo.py"]})
    out = _invoke(
        monkeypatch,
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/foo.py", "old_string": "x"}},
    )
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "fact" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_second_edit_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    update_state(lambda s: {
        **s,
        "read_files": ["/tmp/foo.py"],
        "gated_targets": ["/tmp/foo.py"],
    })
    out = _invoke(
        monkeypatch,
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/foo.py", "old_string": "x"}},
    )
    assert out is None


def test_destructive_bash_is_always_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _invoke(
        monkeypatch,
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/doom"}},
    )
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def _make_repo(tmp_path, name: str = "repo"):
    import subprocess

    repo = tmp_path / name
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    return repo


def test_destructive_first_deny_includes_blast_and_promise(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    repo = _make_repo(tmp_path)
    (repo / "build").mkdir()
    (repo / "build" / "junk.bin").write_text("x" * 50, encoding="utf-8")
    monkeypatch.chdir(repo)

    out = _invoke(
        monkeypatch,
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}},
    )
    assert out is not None
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    # The gate measured the blast radius itself and promises insurance.
    assert "measured the blast radius" in reason
    assert "1 file(s)" in reason
    assert "ONLY in the working tree" in reason
    assert "Insurance:" in reason


def test_destructive_retry_passes_with_verified_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import json as _json
    import subprocess

    from gateguard import log as log_mod

    repo = _make_repo(tmp_path)
    (repo / "build").mkdir()
    (repo / "build" / "junk.bin").write_text("precious uncommitted data", encoding="utf-8")
    monkeypatch.chdir(repo)

    payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}
    first = _invoke(monkeypatch, payload)
    assert first is not None  # ceremony

    second = _invoke(monkeypatch, payload)
    assert second is None  # insured pass

    # The allow was logged with a verified certificate...
    records = [
        _json.loads(ln)
        for ln in log_mod.GATE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    ]
    insured = [r for r in records if r.get("gate") == "destructive_insured"]
    assert len(insured) == 1
    cert = insured[0]["extra"]["certificate"]
    assert cert["verified"] is True
    assert cert["covered_unbacked"] == 1
    # ...and the snapshot actually contains the unbacked file.
    listing = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", cert["commit"]],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "build/junk.bin" in listing


def test_destructive_retry_outside_repo_stays_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    repo = _make_repo(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "data.txt").write_text("x", encoding="utf-8")
    monkeypatch.chdir(repo)

    payload = {"tool_name": "Bash", "tool_input": {"command": f"rm -rf {outside}"}}
    first = _invoke(monkeypatch, payload)
    assert first is not None

    second = _invoke(monkeypatch, payload)
    assert second is not None  # fail closed: uninsurable target
    reason = second["hookSpecificOutput"]["permissionDecisionReason"]
    assert "insurance could not be secured" in reason
    assert "outside the git worktree" in reason


def test_destructive_retry_without_git_repo_stays_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "junk.txt").write_text("x", encoding="utf-8")
    monkeypatch.chdir(plain)

    payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf junk.txt"}}
    first = _invoke(monkeypatch, payload)
    assert first is not None

    second = _invoke(monkeypatch, payload)
    assert second is not None
    reason = second["hookSpecificOutput"]["permissionDecisionReason"]
    assert "insurance could not be secured" in reason


def test_script_bypass_is_scanned_and_gated(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Writing the rm into a script and running the script must not skip
    the gate — the content of what executes is scanned."""
    repo = _make_repo(tmp_path)
    (repo / "build").mkdir()
    (repo / "build" / "junk.bin").write_text("x", encoding="utf-8")
    (repo / "cleanup.sh").write_text("#!/bin/sh\nrm -rf build\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    payload = {"tool_name": "Bash", "tool_input": {"command": "bash cleanup.sh"}}
    first = _invoke(monkeypatch, payload)
    assert first is not None
    reason = first["hookSpecificOutput"]["permissionDecisionReason"]
    assert "cleanup.sh" in reason
    assert "scanned the script" in reason
    assert "measured the blast radius" in reason

    # Retry follows the same insured path as command-line destruction.
    second = _invoke(monkeypatch, payload)
    assert second is None


@pytest.mark.parametrize("command", [
    # Regression fleet for the trailing-\b class of pattern bugs: every
    # one of these slipped through the v0.1–v0.7.0 pattern.
    "rm -fr build",
    "rm -r build",
    "rm -f precious.txt",
    "rm -v -rf build",
    "dd if=/dev/zero of=/dev/sda",
    "git checkout -- file.txt",
    "git clean -fd",
    # Shell-channel self-protection.
    "rm .gateguard.yml",
    "echo 'enabled: false' > .gateguard.yml",
    "sed -i 's/true/false/' .gateguard.yml",
    "echo '{}' > ~/.claude/settings.json",
    # 2026 field-report classes: infra teardown and git clean variants.
    "terraform destroy -auto-approve",
    "terraform state rm aws_db_instance.main",
    "terraform apply -input=false -auto-approve",
    "terraform apply tfplan",             # saved plan applies with no confirm
    "terraform apply -input=false prod.tfplan",
    "tofu destroy",
    "tofu apply plan.out",
    "git clean -d -f",
    "git clean -fdx",
    "git clean --force",
    "git clean -d --force",
])
def test_destructive_pattern_regressions(command: str) -> None:
    from gateguard.hook import BUILTIN_DESTRUCTIVE_BASH

    assert BUILTIN_DESTRUCTIVE_BASH.search(command), command


@pytest.mark.parametrize("command", [
    "npm install rimraf",     # installing the package is not destruction
    "rm --help",
    "rm todo.txt",            # plain single-file rm stays routine
    "echo done > output.log",  # redirects to ordinary files stay routine
    "git checkout --force-help-text",
    "the file was truncated",
    "terraform plan",           # look-only infra ops stay routine
    "terraform apply",          # interactive apply keeps its own confirm
    "terraform apply -var region=us",  # key=value arg is not a plan file
    "terraform state list",
    "git clean -n",             # dry run
])
def test_non_destructive_commands_stay_clean(command: str) -> None:
    from gateguard.hook import BUILTIN_DESTRUCTIVE_BASH

    assert not BUILTIN_DESTRUCTIVE_BASH.search(command), command


def test_binary_executable_is_not_script_scanned(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Directly-executed binaries (NUL bytes) are skipped by the script
    scanner instead of being pattern-matched as garbage text."""
    from gateguard.hook import BUILTIN_DESTRUCTIVE_BASH, _destructive_script

    binary = tmp_path / "tool"
    binary.write_bytes(b"\x7fELF\x00\x00rm -rf /")
    path, _ = _destructive_script(f"{binary}", BUILTIN_DESTRUCTIVE_BASH)
    assert path is None


def test_inline_language_deletion_is_destructive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _invoke(
        monkeypatch,
        {"tool_name": "Bash",
         "tool_input": {"command": "python -c \"import shutil; shutil.rmtree('build')\""}},
    )
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_find_delete_is_destructive(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _invoke(
        monkeypatch,
        {"tool_name": "Bash",
         "tool_input": {"command": "find . -name '*.log' -delete"}},
    )
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_allowed_write_backs_up_old_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import json as _json
    import subprocess

    from gateguard import log as log_mod
    from gateguard.state import update_state

    repo = _make_repo(tmp_path)
    target = repo / "tracked.txt"
    target.write_text("uncommitted precious content\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    # Past the gate already: this Write is allowed and will overwrite.
    update_state(lambda s: {
        **s,
        "read_files": [str(target)],
        "gated_targets": [str(target)],
    })
    out = _invoke(
        monkeypatch,
        {"tool_name": "Write",
         "tool_input": {"file_path": str(target), "content": "new content"}},
    )
    assert out is None  # allowed

    records = [
        _json.loads(ln)
        for ln in log_mod.GATE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    ]
    backups = [r for r in records if r.get("gate") == "write_backup"]
    assert len(backups) == 1
    blob = backups[0]["extra"]["backup"]["blob"]
    content = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-p", blob],
        check=True, capture_output=True, text=True,
    ).stdout
    assert content == "uncommitted precious content\n"


def test_gateguard_config_edit_is_high_risk(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Self-protection: editing .gateguard.yml demands user confirmation
    and is never exempted by evidence."""
    from gateguard.state import update_state

    cfg_file = tmp_path / ".gateguard.yml"
    cfg_file.write_text("enabled: true\n", encoding="utf-8")
    update_state(lambda s: {**s, "read_files": [str(cfg_file)]})

    out = _invoke(
        monkeypatch,
        {"tool_name": "Edit",
         "tool_input": {"file_path": str(cfg_file),
                        "old_string": "enabled: true",
                        "new_string": "enabled: false"}},
    )
    assert out is not None
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "high-impact" in reason
    assert "explicit user instruction" in reason


def test_destructive_retry_with_insurance_disabled_keeps_wall(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    repo = _make_repo(tmp_path)
    (repo / ".gateguard.yml").write_text(
        "insurance:\n  snapshot_pass: false\n", encoding="utf-8"
    )
    monkeypatch.chdir(repo)

    payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}
    first = _invoke(monkeypatch, payload)
    second = _invoke(monkeypatch, payload)
    # v0.6.x semantics: every attempt denied.
    assert first is not None and second is not None


def test_routine_bash_gated_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # v0.6.0: `ls -la` is read-only and bypasses the routine gate, so the
    # once-per-session flow is exercised with a mutating command instead.
    first = _invoke(
        monkeypatch,
        {"tool_name": "Bash", "tool_input": {"command": "npm install"}},
    )
    assert first is not None and first["hookSpecificOutput"]["permissionDecision"] == "deny"

    second = _invoke(
        monkeypatch,
        {"tool_name": "Bash", "tool_input": {"command": "npm install"}},
    )
    assert second is None


def test_readonly_bash_never_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    # v0.6.0: read-only commands pass silently even as the session's
    # very first Bash call.
    out = _invoke(
        monkeypatch,
        {"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
    )
    assert out is None


def test_first_write_is_fact_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _invoke(
        monkeypatch,
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/new.py", "content": "x"}},
    )
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
