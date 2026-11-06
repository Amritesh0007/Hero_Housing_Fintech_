#!/usr/bin/env bash
# Render.com build script: installs deps, trains a model (synthetic if no real data), seeds demo users.
set -euo pipefail

echo "=== Installing dependencies ==="
pip install --upgrade pip
pip install -e .

echo "=== Training model ==="
if [ -f data/raw/house_prices.csv ]; then
  echo "Using real training data"
  python -m valuegraph.cli train --csv data/raw/house_prices.csv
else
  echo "No real data found — generating synthetic training data"
  python -m valuegraph.cli synthetic --rows 20000
  python -m valuegraph.cli train --csv data/sample/synthetic_train.csv --iterations 1000
fi

echo "=== Seeding demo users ==="
python - <<'PY'
from valuegraph.api.auth import hash_password
from valuegraph.api.db import User, init_db, session_scope

init_db()
with session_scope() as db:
    for email, role, name in [
        ("uw@hhf.test", "underwriter", "Demo Underwriter"),
        ("rv@hhf.test", "reviewer", "Demo Reviewer"),
    ]:
        if not db.query(User).filter_by(email=email).first():
            db.add(User(
                email=email,
                password_hash=hash_password("demo-password-123"),
                role=role,
                full_name=name,
            ))
            print(f"  Created {role}: {email}")
        else:
            print(f"  Already exists: {email}")
PY

echo "=== Build complete ==="
