#!/usr/bin/env bash
# Ralph Loop - feeds PROMPT.md to claude on every iteration.
# Stop: Ctrl+C between iterations, or `touch .ralph_stop` at repo root.

set -uo pipefail

cd "$(dirname "$0")/.."

ITER=0
SLEEP_BETWEEN="${SLEEP_BETWEEN:-10}"
MAX_ITER="${MAX_ITER:-0}"   # 0 = forever

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

  # Pipe PROMPT.md into claude as stdin. Do NOT redirect stdin to /dev/null —
  # the claude CLI exits immediately on a non-TTY empty stdin.
  cat PROMPT.md | claude -p --dangerously-skip-permissions --output-format text \
    >> logs/ralph_stdout.log 2>> logs/ralph_stderr.log
  RC=$?
  if [[ "$RC" -ne 0 ]]; then
    echo "[iter #$ITER] claude exited with code $