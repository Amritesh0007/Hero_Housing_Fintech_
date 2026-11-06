#!/bin/bash
# One-click demo for macOS: installs deps into .venv, trains if needed, starts the API + web app, opens the browser.
set -e
cd "$(dirname "$0")"

PY=""
for c in "$HOME/miniforge3/bin/python3" python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then PY="$c"; break; fi
done
[ -z "$PY" ] && { echo "Need Python 3.11+"; read -r; exit 1; }
echo "Using $($PY --version)"

# Deactivate conda if active (Anaconda's base env hijacks pip/python)
if command -v conda &>/dev/null; then conda deactivate 2>/dev/null || true; fi

[ -d .venv ] || "$PY" -m venv .venv
source .venv/bin/activate
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/pip install -q -e .

if [ ! -f models/model.joblib ]; then
  if [ -f data/raw/house_prices.csv ]; then valuegraph train --csv data/raw/house_prices.csv
  else valuegraph synthetic && valuegraph train --csv data/sample/synthetic_train.csv; fi
fi

export DATABASE_URL="sqlite:///./demo.db"
.venv/bin/python - <<'PY'
from valuegraph.api.auth import hash_password
from valuegraph.api.db import User, init_db, session_scope
init_db()
with session_scope() as db:
    for email, role in [("uw@hhf.test", "underwriter"), ("rv@hhf.test", "reviewer")]:
        if not db.query(User).filter_by(email=email).first():
            db.add(User(email=email, password_hash=hash_password("demo-password-123"), role=role))
PY

lsof -ti:8000,8501 | xargs kill 2>/dev/null || true

echo
echo "================================================================"
echo " KSHETRA: http://localhost:8000   (lender app: /app/)"
echo "   underwriter  uw@hhf.test / demo-password-123"
echo "   reviewer     rv@hhf.test / demo-password-123"
echo " Close this window (or Ctrl+C) to stop."
echo "================================================================"
(sleep 3 && open http://localhost:8000) &
exec .venv/bin/uvicorn valuegraph.api.main:app --port 8000
