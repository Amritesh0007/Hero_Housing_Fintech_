import os

import pytest


@pytest.fixture(scope="session")
def trained(tmp_path_factory):
    d = tmp_path_factory.mktemp("vg")
    os.environ.update(
        APP_ENV="test", DATABASE_URL=f"sqlite:///{d}/test.db", MODEL_DIR=str(d / "models"), JWT_SECRET="x" * 40
    )
    from valuegraph.config import get_settings

    get_settings.cache_clear()

    from valuegraph.data import clean, make_synthetic
    from valuegraph.train import run

    listings, _ = clean(make_synthetic(4000, seed=1))
    # Synthetic localities "Sector N" laid out ~1 km apart on a line from a per-city origin.
    locs = listings[["city", "locality"]].drop_duplicates()
    coords = locs.assign(
        lat=[17.0 + len(c) + int(loc.split()[-1]) * 0.009 for c, loc in locs.itertuples(index=False)], lon=78.0
    )
    report = run(listings, d / "models", coords=coords, iterations=300)
    return d, listings, report


@pytest.fixture(scope="session")
def model(trained):
    import joblib

    return joblib.load(trained[0] / "models" / "model.joblib")


@pytest.fixture(scope="session")
def client(trained):
    from fastapi.testclient import TestClient

    from valuegraph.api.auth import hash_password
    from valuegraph.api.db import User, init_db, session_scope
    from valuegraph.api.main import app

    init_db()
    with session_scope() as db:
        for role in ("underwriter", "reviewer", "risk", "admin"):
            db.add(User(email=f"{role}@hhf.test", password_hash=hash_password("correct-horse-battery"), role=role))
        db.add(User(email="other@hhf.test", password_hash=hash_password("correct-horse-battery"), role="underwriter"))
    with TestClient(app) as c:
        yield c


def login(client, who: str) -> dict:
    r = client.post("/api/v1/auth/token", json={"email": f"{who}@hhf.test", "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
