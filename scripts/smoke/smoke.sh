#!/usr/bin/env bash
# Smoke test as a user would run it: the server started with the code's
# declared settings, not this shell's overrides. The two a user sets are kept:
# the API key and the SEC contact string, plus the two model names, which
# the user chose (Gemini) and which the eval header prints on every run. Everything else that this shell
# overrides is unset, and the list is derived from Settings.model_fields.
set -euo pipefail
ROOT=${ROOT:-/home/user/pharma-analog-uptake-workbench}
S=$(dirname "$(readlink -f "$0")")
PORT=${PORT:-8011}
cd $ROOT/backend
UNSET=$(./.venv/bin/python - <<'PY'
import os
from app.config import Settings
keep = {"OPENROUTER_API_KEY", "SEC_USER_AGENT", "OPENROUTER_MODEL_EXTRACT", "OPENROUTER_MODEL_JUDGE"}
names = [n.upper() for n in Settings.model_fields]
print(" ".join(f"-u {n}" for n in names if n in os.environ and n not in keep))
PY
)
echo "unsetting: $UNSET" | tee $S/unset.txt
rm -f ./storage/workbench.db
env $UNSET ./.venv/bin/uvicorn app.main:app --port $PORT > $S/server.log 2>&1 &
echo $! > $S/server.pid
for i in $(seq 1 60); do
  curl -sf http://127.0.0.1:$PORT/config >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://127.0.0.1:$PORT/config | ./.venv/bin/python -c '
import json,sys; d=json.load(sys.stdin)
ov=[f for f in d["settings"] if f["overridden"]]
print("overridden at the server:", [(f["field"], f["value"]) for f in ov if "key" not in f["field"]])'
cd $ROOT
for CASES in "$@"; do
  name=$(basename "$CASES" .json)
  echo "=== $CASES -> $S/$name.eval.json"
  ./backend/.venv/bin/python scripts/eval.py --cases "$CASES" --base http://127.0.0.1:$PORT \
      --out "$S/$name.eval.json" 2>&1 | tee "$S/$name.eval.txt"
done
