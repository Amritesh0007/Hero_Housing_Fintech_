from conftest import login

BODY = {
    "city": "Hyderabad",
    "locality": "Sector 3",
    "area_sqft": 1100,
    "area_type": "built_up",
    "bhk": 2,
    "loan_amount_inr": 4_000_000,
    "guideline_rate_sqft": 5000,
}


def test_health_and_unauthenticated(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.post("/api/v1/valuations", json=BODY)
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")


def test_config_exposes_only_the_maps_browser_key(client):
    # Only public browser identifiers (Maps key, Google client ID); no secrets may leak through here.
    body = client.get("/api/v1/config").json()
    assert set(body) == {"maps_api_key", "google_client_id"}


def test_public_estimate_runs_the_model_but_stores_and_decides_nothing(client):
    from valuegraph.api.db import Valuation, session_scope

    with session_scope() as db:
        before = db.query(Valuation).count()
    r = client.post("/api/v1/public/estimate", json=BODY)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["value"] and v["low"] <= v["value"] <= v["high"] and v["factors"]
    # Lending decisions and lender inputs stay behind sign-in.
    assert not {"route", "lending", "guideline", "valuation_id"} & set(v)
    with session_scope() as db:
        assert db.query(Valuation).count() == before


def test_public_market_and_model_endpoints(client):
    cities = client.get("/api/v1/public/cities").json()
    assert "Hyderabad" in cities
    s = client.get("/api/v1/public/markets/Hyderabad/summary").json()
    assert s["listings"] > 0 and s["localities"]
    assert client.get("/api/v1/public/markets/Atlantis/summary").status_code == 404
    m = client.get("/api/v1/public/model").json()
    assert set(m) >= {"median_error", "within_10pct", "interval_coverage", "model_version"}


def test_landing_page_is_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200 and "KSHETRA" in r.text


def test_every_api_route_requires_auth(client):
    from valuegraph.api.main import app

    open_routes = {"/api/v1/auth/token", "/api/v1/auth/google", "/api/v1/config"}
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/") and path not in open_routes and not path.startswith("/api/v1/public/"):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                url = path.replace("{vid}", "VAL-X").replace("{rid}", "1").replace("{city}", "Hyderabad")
                assert client.request(method, url).status_code in (401, 422), (method, path)


def test_bad_login(client):
    r = client.post("/api/v1/auth/token", json={"email": "nobody@hhf.test", "password": "wrong-password-x"})
    assert r.status_code == 401


def test_valuation_flow_review_and_audit(client):
    uw = login(client, "underwriter")
    r = client.post("/api/v1/valuations", json=BODY, headers={**uw, "Idempotency-Key": "k1"})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["valuation_id"].startswith("VAL-") and v["basis"] == "asking_price_proxy"
    assert v["area"]["listing_basis_sqft"] == 1320  # 1100 built-up × 1.20
    assert v["guideline"]["rate_per_sqft"] == 5000
    assert v["lending"]["ltv_basis"] == v["low"]

    # Idempotency: same key + same body -> same valuation; different body -> 409
    again = client.post("/api/v1/valuations", json=BODY, headers={**uw, "Idempotency-Key": "k1"})
    assert again.json()["valuation_id"] == v["valuation_id"]
    clash = client.post("/api/v1/valuations", json={**BODY, "bhk": 3}, headers={**uw, "Idempotency-Key": "k1"})
    assert clash.status_code == 409

    # Another underwriter cannot see it; risk can
    assert client.get(f"/api/v1/valuations/{v['valuation_id']}", headers=login(client, "other")).status_code == 404
    assert client.get(f"/api/v1/valuations/{v['valuation_id']}", headers=login(client, "risk")).status_code == 200

    # Underwriter can't review; reviewer must give a reason to override
    if v["route"] != "auto":
        rv = login(client, "reviewer")
        queue = client.get("/api/v1/reviews", headers=rv).json()
        rid = next(q["review_id"] for q in queue if q["valuation_id"] == v["valuation_id"])
        assert client.patch(f"/api/v1/reviews/{rid}", json={"action": "accept"}, headers=uw).status_code == 403
        bad = client.patch(f"/api/v1/reviews/{rid}", json={"action": "override", "override_value_inr": 1}, headers=rv)
        assert bad.status_code == 422
        ok = client.patch(
            f"/api/v1/reviews/{rid}",
            json={"action": "override", "override_value_inr": 6_500_000, "reason": "Site visit: needs repairs"},
            headers=rv,
        )
        assert ok.json()["status"] == "overridden"
        assert client.patch(f"/api/v1/reviews/{rid}", json={"action": "accept"}, headers=rv).status_code == 409

    trail = client.get(f"/api/v1/valuations/{v['valuation_id']}/audit", headers=login(client, "risk")).json()
    assert trail["chain_valid"] and trail["events"][0]["action"] == "valuation.create"
    assert client.get(f"/api/v1/valuations/{v['valuation_id']}/audit", headers=uw).status_code == 403


def test_validation_rejects_unknown_fields_and_ranges(client):
    uw = login(client, "underwriter")
    assert client.post("/api/v1/valuations", json={**BODY, "hack": 1}, headers=uw).status_code == 422
    assert (
        client.post("/api/v1/valuations", json={**BODY, "floor": 9, "total_floors": 4}, headers=uw).status_code == 422
    )
    body = {k: v for k, v in BODY.items() if k != "area_type"}
    assert client.post("/api/v1/valuations", json=body, headers=uw).status_code == 422


def test_api_key_auth(client):
    from valuegraph.api.auth import new_api_key
    from valuegraph.api.db import ApiKey, User, session_scope

    with session_scope() as db:
        uid = db.query(User).filter_by(email="underwriter@hhf.test").one().id
        raw, prefix, digest = new_api_key()
        db.add(ApiKey(user_id=uid, prefix=prefix, key_hash=digest))
    assert client.get("/api/v1/me", headers={"X-API-Key": raw}).json()["via"] == f"api_key:{prefix}"
    assert client.get("/api/v1/me", headers={"X-API-Key": raw[:-2] + "zz"}).status_code == 401


def test_market_and_model_status(client):
    h = login(client, "underwriter")
    assert client.get("/api/v1/markets/Hyderabad/summary", headers=h).json()["price_source"] == "asking"
    status = client.get("/api/v1/models/status", headers=h).json()
    assert status["ground_truth"] == "asking_price_proxy" and "Hyderabad" in status["cities"]


def test_login_lockout(client):
    for _ in range(5):
        client.post("/api/v1/auth/token", json={"email": "admin@hhf.test", "password": "nope-nope-nope"})
    r = client.post("/api/v1/auth/token", json={"email": "admin@hhf.test", "password": "correct-horse-battery"})
    assert r.status_code == 429


def test_malformed_content_length_is_400_not_500(client):
    r = client.post("/api/v1/auth/token", content=b"{}", headers={"Content-Length": "abc"})
    assert r.status_code in (400, 422)  # never a 500


def test_valuation_is_persisted_before_response(client):
    from valuegraph.api.db import Valuation, session_scope

    r = client.post("/api/v1/valuations", json=BODY, headers=login(client, "underwriter"))
    with session_scope() as db:
        assert db.get(Valuation, r.json()["valuation_id"]) is not None


def test_rejects_absurd_guideline_rate(client):
    r = client.post(
        "/api/v1/valuations", json={**BODY, "guideline_rate_sqft": 0.01}, headers=login(client, "underwriter")
    )
    assert r.status_code == 422


def test_web_app_is_served(client):
    page = client.get("/app/")
    assert page.status_code == 200 and "KSHETRA" in page.text
    assert client.get("/app/app.js").status_code == 200


def test_valuation_carries_location_and_neighbourhood(client):
    v = client.post("/api/v1/valuations", json=BODY, headers=login(client, "underwriter")).json()
    assert v["location"] and {"lat", "lon"} <= v["location"].keys()
    assert v["neighbourhood"] and all(n["locality"] != BODY["locality"] for n in v["neighbourhood"])
    market = client.get("/api/v1/markets/Hyderabad/summary", headers=login(client, "underwriter")).json()
    assert any(loc["lat"] is not None for loc in market["localities"])


def test_society_is_optional_and_used_when_given(client):
    h = login(client, "underwriter")
    without = client.post("/api/v1/valuations", json=BODY, headers=h).json()
    with_soc = client.post("/api/v1/valuations", json={**BODY, "society": "demo residency"}, headers=h).json()
    assert without["value"] and with_soc["value"] and with_soc["value"] != without["value"]
    assert any(f["label"] == "Same building / project" for f in with_soc["factors"])
    too_long = client.post("/api/v1/valuations", json={**BODY, "society": "x" * 121}, headers=h)
    assert too_long.status_code == 422


# ---- Sign in with Google ------------------------------------------------------------------------
def _google_token(key, **over):
    import time

    import jwt

    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "test-client.apps.googleusercontent.com",
        "sub": "1234",
        "email": "Priya.New@gmail.com",
        "email_verified": True,
        "iat": now,
        "exp": now + 600,
        **over,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def _fake_google_keys(monkeypatch, public_key):
    from types import SimpleNamespace

    from valuegraph.api import auth

    monkeypatch.setattr(auth._google_keys, "get_signing_key_from_jwt", lambda _t: SimpleNamespace(key=public_key))


def test_google_token_verification_rejects_forgeries(monkeypatch):
    import jwt
    import pytest
    from cryptography.hazmat.primitives.asymmetric import rsa

    from valuegraph.api.auth import verify_google_id_token

    google, attacker = (rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))
    _fake_google_keys(monkeypatch, google.public_key())
    aud = "test-client.apps.googleusercontent.com"
    assert verify_google_id_token(_google_token(google), aud)["email"] == "Priya.New@gmail.com"
    for bad in (
        _google_token(attacker),  # not signed by Google
        _google_token(google, aud="someone-elses-app"),  # issued for a different app
        _google_token(google, iss="https://evil.example"),
        _google_token(google, exp=1),  # expired
        _google_token(google, email_verified=False),
    ):
        with pytest.raises(jwt.InvalidTokenError):
            verify_google_id_token(bad, aud)


def test_google_login_creates_an_underwriter_then_signs_them_in(client, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa

    from valuegraph.config import get_settings

    google = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _fake_google_keys(monkeypatch, google.public_key())
    monkeypatch.setattr(get_settings(), "google_client_id", "test-client.apps.googleusercontent.com")

    r = client.post("/api/v1/auth/google", json={"credential": _google_token(google)})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "underwriter" and r.json()["email"] == "priya.new@gmail.com"
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["email"] == "priya.new@gmail.com"
    # Second sign-in reuses the same account.
    again = client.post("/api/v1/auth/google", json={"credential": _google_token(google)}).json()
    assert again["role"] == "underwriter"
    # A forged token gets nothing.
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert client.post("/api/v1/auth/google", json={"credential": _google_token(attacker)}).status_code == 401


def test_google_login_is_off_until_configured(client):
    r = client.post("/api/v1/auth/google", json={"credential": "x" * 40})
    assert r.status_code == 404
