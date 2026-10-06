#!/usr/bin/env bash
# Export an up-to-date gold verification tracker: snapshot the verification
# app's verdicts, then rebuild exports/gold_verification_tracker.xlsx from the
# current gold with those verdicts filled in.
#
#   scripts/sourcing/export_gold_tracker.sh                 # pull from Supabase
#   scripts/sourcing/export_gold_tracker.sh verdicts.csv    # or from the app's CSV download
#
# Pulling from Supabase needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the
# environment. With neither those nor a CSV, the last committed snapshot
# (docs/sourcing/human_verdicts.json) is used as it is.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-backend/.venv/bin/python}"
[ -x "$PY" ] || PY=python3

if [ $# -ge 1 ]; then
  "$PY" scripts/sourcing/pull_verdicts.py --from-csv "$1"
elif [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_SERVICE_ROLE_KEY:-}" ]; then
  "$PY" scripts/sourcing/pull_verdicts.py --from-supabase
else
  echo "no CSV given and no Supabase credentials: using the existing verdict snapshot" >&2
fi
"$PY" scripts/sourcing/build_verification_tracker.py
