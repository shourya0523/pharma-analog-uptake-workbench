#!/usr/bin/env bash
# Detached gold + jev-holdout smoke. Avoids `pkill -f uvicorn app.main:app`.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORT="${SMOKE_PORT:-18765}"
E="$ROOT/backend/.venv/bin/python"
DB="/tmp/pharma_smoke_${PORT}.db"
FILES="/tmp/pharma_smoke_${PORT}_files"
LOG="/tmp/pharma_smoke_${PORT}.server.log"
OUT="/tmp/pharma_smoke_${PORT}.runner.out"
S="$ROOT/scripts/smoke"
UPID="/tmp/pharma_smoke_${PORT}.uvicorn.pid"
RPID="/tmp/pharma_smoke_${PORT}.runner.pid"

echo $$ > "$RPID"
lsof -ti ":$PORT" | xargs kill -9 2>/dev/null || true
sleep 1
rm -f "$DB" "${DB}-journal" "${DB}-wal" "${DB}-shm"
rm -rf "$FILES"
mkdir -p "$FILES"
: > "$LOG"
rm -f "$S/gold_sample.eval.json" "$S/holdout_2026_09_22_jev.eval.json"
: > "$S/gold_sample.eval.txt"
: > "$S/holdout_2026_09_22_jev.eval.txt"

cd "$ROOT/backend"
PYTHONPATH="$ROOT/backend" \
SMOKE_PORT="$PORT" \
DATABASE_URL="sqlite+aiosqlite:///$DB" \
LOCAL_STORAGE_ROOT="$FILES" \
MAX_CONCURRENT_JOBS=1 \
STORAGE_BACKEND=local \
JOB_BACKEND=inprocess \
AWS_PROFILE=default \
"$E" "$S/serve_smoke.py" >>"$LOG" 2>&1 &
echo $! > "$UPID"
echo "serve_pid=$(cat "$UPID") port=$PORT"

for i in $(seq 1 90); do
  curl -sf -m 2 "http://127.0.0.1:$PORT/health" >/dev/null && break
  kill -0 "$(cat "$UPID")" 2>/dev/null || { echo 'server died'; tail -40 "$LOG"; exit 1; }
  sleep 1
done
curl -sf -m 5 "http://127.0.0.1:$PORT/health" >/dev/null
echo SERVER_READY

cd "$ROOT"
echo "=== gold_sample ==="
"$E" scripts/eval.py --cases seed/cases/gold_sample.json \
  --base "http://127.0.0.1:$PORT" --timeout 7200 \
  --out "$S/gold_sample.eval.json" 2>&1 | tee "$S/gold_sample.eval.txt"
echo GOLD_EXIT:$?

echo "=== holdout_2026_09_22_jev ==="
"$E" scripts/eval.py --cases seed/cases/holdout_2026_09_22_jev.json \
  --base "http://127.0.0.1:$PORT" --timeout 7200 \
  --out "$S/holdout_2026_09_22_jev.eval.json" 2>&1 | tee "$S/holdout_2026_09_22_jev.eval.txt"
echo HOLDOUT_EXIT:$?
echo SMOKE_DONE
