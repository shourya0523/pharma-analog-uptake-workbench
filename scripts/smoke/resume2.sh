#!/usr/bin/env bash
# Resume smoke run 2 on the database the dead server left: attach to the
# gold-sample runs already there, then the held-out set without CIKs.
set -uo pipefail
S=$(dirname "$(readlink -f "$0")"); ROOT=${ROOT:?}; PORT=${PORT:-8011}
cd $ROOT/backend
UNSET=$(sed 's/^unsetting: //' $S/unset.txt)
env $UNSET ./.venv/bin/uvicorn app.main:app --port $PORT >> $S/server.log 2>&1 &
echo $! > $S/server.pid
for i in $(seq 1 60); do curl -sf http://127.0.0.1:$PORT/config >/dev/null 2>&1 && break; sleep 1; done
cd $ROOT; E=./backend/.venv/bin/python
echo "=== gold_sample (attach)"; $E scripts/eval.py --cases seed/cases/gold_sample.json --attach --timeout 7200 --base http://127.0.0.1:$PORT --out $S/gold_sample.eval.json 2>&1 | tee $S/gold_sample.eval.txt
echo "=== holdout without cik (attach)"; $E scripts/eval.py --cases $S/holdout_2026_09_no_cik.json --attach --timeout 7200 --base http://127.0.0.1:$PORT --out $S/holdout_no_cik.eval.json 2>&1 | tee $S/holdout_no_cik.eval.txt
