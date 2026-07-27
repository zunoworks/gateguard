#!/bin/bash
# PainBench generic runner — one task, gated vs ungated, headless.
#
# Usage: run_task.sh <task_dir_name> [model]
#   e.g. run_task.sh t2_scope claude-opus-5
#
# See run_pilot.sh header for the environment rationale (clean env,
# no set -u on bash 3.2, --settings for the gated hooks).
set -eo pipefail

BENCH="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$BENCH/../.." && pwd)"
TASK="${1:?usage: run_task.sh <task> [model]}"
MODEL="${2:-claude-opus-5}"
RUNS="${PAINBENCH_RUNS:-/tmp/painbench_runs}/${TASK}_$(date +%m%d_%H%M%S)"
VENV="$BENCH/.venv-subject"
WHEEL="$REPO/dist/gateguard_ai-0.6.0-py3-none-any.whl"
TASK_DIR="$BENCH/tasks/$TASK"

[ -d "$TASK_DIR/fixture" ] || { echo "unknown task: $TASK"; exit 1; }
mkdir -p "$RUNS"

if [ ! -x "$VENV/bin/gateguard-hook" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" -q install "$WHEEL"
fi

for ARM in ungated gated; do
  WORK="$RUNS/$ARM"
  mkdir -p "$WORK"
  cp -R "$TASK_DIR/fixture/." "$WORK/"
  git -C "$WORK" init -q
  git -C "$WORK" add -A
  git -C "$WORK" -c user.email=bench@painbench -c user.name=painbench commit -qm baseline

  SETTINGS_ARGS=""
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
    SETTINGS_ARGS="--settings $WORK/.claude/settings.json"
  fi

  echo "=== $TASK/$ARM ($MODEL) starting $(date +%H:%M:%S) ==="
  (
    cd "$WORK"
    env -i HOME="$HOME" PATH="$PATH" USER="$USER" TERM=xterm \
      CORREX_DISABLED=1 claude -p "$(cat "$TASK_DIR/task_prompt.md")" \
      --model "$MODEL" \
      --allowedTools "Read" "Grep" "Glob" "Edit" "Write" "Bash" \
      --max-turns 40 \
      --output-format stream-json --verbose \
      $SETTINGS_ARGS \
      > "$RUNS/${ARM}_transcript.jsonl" 2> "$RUNS/${ARM}_stderr.log"
  ) || echo "claude exited non-zero for $ARM (see ${ARM}_stderr.log)"
  echo "=== $TASK/$ARM done $(date +%H:%M:%S) ==="
done

echo "RUNS_DIR=$RUNS"
for ARM in ungated gated; do
  echo "--- $TASK/$ARM verdict ---"
  python3 "$TASK_DIR/score.py" "$RUNS/$ARM" "$RUNS/${ARM}_transcript.jsonl" || true
done
