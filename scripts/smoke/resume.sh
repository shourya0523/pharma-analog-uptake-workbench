#!/usr/bin/env bash
# Resume the smoke run on the database the killed server left behind: no rm,
# same settings, score the held-out runs already there with --attach, then
# gold fresh with a clock long enough for its 61 jobs, then the held-out set
# again without CIKs.
set -uo pipefail
S=$(dirname "$(readlink -f "$0")")
ROOT=${ROOT:?}
PORT=${PORT:-8011}
cd $ROOT/backend
UNSET=$(cat $S/unset.txt | sed 's/^unsetting: //')
env $UNSET ./.venv/bin/uvicorn app.main:app --port $PORT >> $S/server.log 2>&1 &
echo $! > $S/server.pid
for i in $(seq 1 60); do curl -sf http://127.0.0.1:$PORT/config >/dev/null 2>&1 && break; sleep 1; done
cd $ROOT
E=./backend/.venv/bin/python
echo "=== holdout (attach)"; $E scripts/eval.py --cases seed/cases/holdout_2026_09.json --attach --timeout 7200 --base http://127.0.0.1:$PORT --out $S/holdout_2026_09.eval.json 2>&1 | tee $S/holdout_2026_09.eval.txt
echo "=== gold"; $E scripts/eval.py --cases seed/cases/gold_all.json --timeout 21600 --base http://127.0.0.1:$PORT --out $S/gold_all.eval.json 2>&1 | tee $S/gold_all.eval.txt
echo "=== holdout without cik"; $E scripts/eval.py --cases $S/holdout_2026_09_no_cik.json --timeout 7200 --base http://127.0.0.1:$PORT --out $S/holdout_no_cik.eval.json 2>&1 | tee $S/holdout_no_cik.eval.txt
