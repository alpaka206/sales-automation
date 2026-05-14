#!/usr/bin/env bash
# Ralph Loop — runs claude on PROMPT.md repeatedly until stopped.
# Stop: Ctrl+C between iterations, or `touch .ralph_stop` at repo root.

set -uo pipefail

cd "$(dirname "$0")/.."

ITER=0
SLEEP_BETWEEN="${SLEEP_BETWEEN:-10}"
MAX_ITER="${MAX_ITER:-0}"  # 0 = forever

mkdir -p logs

while :; do
  ITER=$((ITER + 1))
  echo
  echo "======================================================"
  echo " Ralph iteration #$ITER  ($(date '+%Y-%m-%d %H:%M:%S'))"
  echo "======================================================"

  if [[ -f .ralph_stop ]]; then
    echo "Stop file detected. Exiting."
    rm -f .ralph_stop
    break
  fi

  claude -p "@PROMPT.md" --dangerously-skip-permissions --output-format text \
    >> logs/ralph_stdout.log 2>> logs/ralph_stderr.log || true

  if [[ "$MAX_ITER" -ne 0 && "$ITER" -ge "$MAX_ITER" ]]; then
    echo "Reached MAX_ITER=$MAX_ITER. Exiting."
    break
  fi

  echo "Sleeping ${SLEEP_BETWEEN}s before next iteration..."
  sleep "$SLEEP_BETWEEN"
done
