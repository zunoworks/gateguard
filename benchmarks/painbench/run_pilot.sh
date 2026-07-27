#!/bin/bash
# PainBench T1 pilot — gated (gateguard-ai 0.6.0) vs ungated, headless.
#
# Usage: run_pilot.sh [model]         (default: claude-opus-5)
# Env:   PAINBENCH_RUNS=<dir>         (default: /tmp/painbench_runs)
#
# Notes:
# - CORREX_DISABLED=1 turns off the machine-local personal gate so the
#   only gating variable is the packaged gateguard-ai in the gated arm.
# - The gated arm loads hooks via --settings (explicit opt-in; project
#   settings are not silently trusted in headless mode).
# - Subjects run with a turn cap so a retry loop cannot burn quota.
# - No `set -u`: empty-array expansion under -u errors on macOS bash 3.2
#   (observed live 2026-07-27 — killed the ungated arm before launch).
# - `env -u CLAUDECODE`: nested `claude -p` inside a Claude Code session
#   fails OAuth refresh unless the parent marker env is stripped
#   (April 2026 subprocess notes, reconfirmed live today).
set -eo pipefail

BENCH="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$BENCH/../.." && pwd)"
MODEL="${1:-claude-opus-5}"
RUNS="${PAINBENCH_RUNS:-/tmp/painbench_runs}/t1_$(date +%m%d_%H%M%S)"
VENV="$BENCH/.venv-subject"
WHEEL="$REPO/dist/gateguard_ai-0.6.0-py3-none-any.whl"

mkdir -p "$RUNS"

if [ ! -x "$VENV/bin/gateguard-hook" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" -q install "$WHEEL"
fi

for ARM in ungated gated; do
  WORK="$RUNS/$ARM"
  mkdir -p "$WORK"
  cp -R "$BENCH/tasks/t1_collateral/fixture/." "$WORK/"
  git -C "$WORK" init -q
  git -C "$WORK" add -A
  git -C "$WORK" -c user.email=bench@painbench -c user.name=painbench commit -qm baseline

  SETTINGS_ARGS=()
  if [ "$ARM" = "gated" ]; then
    GG_STATE="$WORK/.ggstate"
    mkdir -p "$WORK/.claude"
    cat > "$WORK/.claude/settings.json" <<EOF
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|Bash",
        "hooks": [
          {"type": "command",
           "command": "GATEGUARD_STATE_DIR=$GG_STATE $VENV/bin/gateguard-hook",
           "timeout": 3000}
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Read|Grep|Glob|Bash",
        "hooks": [
          {"type": "command",
           "command": "GATEGUARD_STATE_DIR=$GG_STATE $VENV/bin/gateguard-read-tracker",
           "timeout": 3000}
        ]
      }
    ]
  }
}
EOF
    SETTINGS_ARGS=(--settings "$WORK/.claude/settings.json")
  fi

  echo "=== $ARM ($MODEL) starting $(date +%H:%M:%S) ==="
  (
    cd "$WORK"
    # Fully clean env: the parent app session injects ANTHROPIC_BASE_URL +
    # CLAUDE_CODE_* auth vars that point the nested CLI at the app's
    # endpoint with app-session credentials (observed: OAuth refresh
    # failure). env -i + fresh CLI login is the working combination.
    env -i HOME="$HOME" PATH="$PATH" USER="$USER" TERM=xterm \
      CORREX_DISABLED=1 claude -p "$(cat "$BENCH/tasks/t1_collateral/task_prompt.md")" \
      --model "$MODEL" \
      --allowedTools "Read" "Grep" "Glob" "Edit" "Write" "Bash" \
      --max-turns 40 \
      --output-format stream-json --verbose \
      "${SETTINGS_ARGS[@]}" \
      > "$RUNS/${ARM}_transcript.jsonl" 2> "$RUNS/${ARM}_stderr.log"
  ) || echo "claude exited non-zero for $ARM (see ${ARM}_stderr.log)"
  echo "=== $ARM done $(date +%H:%M:%S) ==="
done

echo "RUNS_DIR=$RUNS"
for ARM in ungated gated; do
  echo "--- $ARM verdict ---"
  python3 "$BENCH/tasks/t1_collateral/score.py" "$RUNS/$ARM" "$RUNS/${ARM}_transcript.jsonl" || true
done
