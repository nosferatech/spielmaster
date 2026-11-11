#!/usr/bin/env bash
# Phase-0 one-shot worker run: ./run.sh <task-key>
# Keys: smoke task1 task2 task3 task4 task5 approval_probe (see tasks.json)
set -uo pipefail
PHASE0="$(cd "$(dirname "$0")" && pwd)"
T=${1:?task key}
TASK=$(python3 -c "import json,sys; print(json.load(open('$PHASE0/tasks.json'))[sys.argv[1]])" "$T") || { echo "unknown task key: $T"; exit 2; }
WORK="$PHASE0/work/$T"
RESULTS="$PHASE0/results"
mkdir -p "$RESULTS" "$PHASE0/work"
rm -rf "$WORK"
cp -a "$PHASE0/fixtures" "$WORK"
(cd "$WORK" && git init -q && git add -A && git commit -qm baseline) 2>/dev/null
LOCAL_API_KEY=$(strata launch --show-key 2>/dev/null | tail -1)
export LOCAL_API_KEY
START=$(date +%s)
cd "$WORK"
timeout 900 dsh --profile headless --json --approval-policy never "$TASK" > "$RESULTS/$T.ndjson" 2> "$RESULTS/$T.err"
EXIT=$?
echo "$T exit=$EXIT secs=$(( $(date +%s) - START )) ndjson=$(wc -l < "$RESULTS/$T.ndjson")" >> "$RESULTS/summary.txt"
