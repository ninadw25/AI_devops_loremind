#!/usr/bin/env bash
# Overnight full Gmail → L0 payload replica for ezobooks.
# Auto-restarts on any non-zero exit (a failed mailbox, a transient network
# blip) — per-mailbox cursors make each restart a cheap delta for the users
# already done, so the night keeps making progress. Stops when a run finishes
# with every mailbox succeeded (exit 0).
set -u

cd /home/zeiko/work/loremind/lore/lore-ingestion || exit 1
PY=../.venv/bin/python
LOG=output/logs/full_sync_driver.log
MAX_ATTEMPTS=40

echo "==================================================================" >>"$LOG"
echo "=== FULL SYNC START $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="              >>"$LOG"
echo "==================================================================" >>"$LOG"

for i in $(seq 1 "$MAX_ATTEMPTS"); do
    echo "--- attempt $i/$MAX_ATTEMPTS  $(date -u +%H:%M:%SZ) ---" >>"$LOG"
    $PY -m runners.ledger_sync \
        --org ezobooks --domain ezobooks.in \
        --admin-email gk@ezobooks.in --key key.json \
        --users-file output/config/ezobooks_gmail_users.json \
        --output-dir ./output >>"$LOG" 2>&1
    code=$?
    if [ "$code" -eq 0 ]; then
        echo "=== SYNC COMPLETE — all mailboxes done $(date -u +%H:%M:%SZ) ===" >>"$LOG"
        break
    fi
    echo "--- attempt $i exited $code; failed mailboxes retry via cursor; sleeping 60s ---" >>"$LOG"
    sleep 60
done
echo "=== DRIVER EXIT $(date -u +%H:%M:%SZ) ===" >>"$LOG"
