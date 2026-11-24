#!/usr/bin/env bash
# Overnight claim extraction for ezobooks — multi-message conversations first.
# Resumable: a conversation with a digest row is skipped, so every restart is a
# cheap delta. Auto-restarts on any non-zero exit (DB blip, API stall); stops
# clean when a run finds nothing left to do (exit 0, "nothing to do").
set -u
cd /home/zeiko/work/loremind/lore/lore-ingestion || exit 1
PY=../.venv/bin/python
LOG=output/logs/extract_claims_driver.log
MAX_ATTEMPTS=60
MIN_MESSAGES="${1:-2}"     # default: skip single-message notification tier

echo "=== CLAIMS START $(date -u +%Y-%m-%dT%H:%M:%SZ) min_messages=$MIN_MESSAGES ===" >>"$LOG"
for i in $(seq 1 "$MAX_ATTEMPTS"); do
    echo "--- attempt $i/$MAX_ATTEMPTS $(date -u +%H:%M:%SZ) ---" >>"$LOG"
    # -u: unbuffered stdout so the log streams live instead of arriving in
    # stale 8KB chunks. Concurrency is adaptive (AIMD) from 12 up to 48.
    $PY -u -m runners.extract_claims --org ezobooks \
        --concurrency 12 --max-concurrency 48 --batch 400 \
        --min-messages "$MIN_MESSAGES" >>"$LOG" 2>&1
    code=$?
    if [ "$code" -eq 0 ]; then
        echo "=== CLAIMS COMPLETE $(date -u +%H:%M:%SZ) ===" >>"$LOG"
        break
    fi
    echo "--- attempt $i exited $code; resuming after 30s ---" >>"$LOG"
    sleep 30
done
echo "=== CLAIMS DRIVER EXIT $(date -u +%H:%M:%SZ) ===" >>"$LOG"
