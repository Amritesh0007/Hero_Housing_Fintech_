import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import joblib
import jwt
import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import i18n
from ..config import get_settings
from ..data import LOADING
from ..engine import value_property
from .auth import (
    Principal,
    check_password,
    create_token,
    current_principal,
    hash_password,
    require,
    reset_throttle,
    throttle,
    verify_google_id_token,
)
from .db import AuditLog, Review, User, Valuation, audit, get_db, init_db, now, verify_audit_chain

STAFF = ("reviewer", "risk")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Strict):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class ValuationIn(Strict):
    city: str = Field(min_length=2, max_length=60)
    locality: str = Field(min_length=2, max_length=120)
    society: str | None = Field(None, max_length=120)  # building / project name; sharpens the estimate
    property_type: Literal["apartment"] = "apartment"
    area_sqft: float = Field(gt=100, le=20_000)
    area_type: Literal["carpet", "built_up", "super_built_up"]
    bhk: int = Field(ge=1, le=10)
    bathrooms: int | None = Field(None, ge=1, le=8)
    balconies: int | None = Field(None, ge=0, le=6)
    floor: int | None = Field(None, ge=-2, le=200)
    total_floors: int | None = Field(None, ge=1, le=200)
    parking: int | None = Field(None, ge=0, le=5)
    parking_type: Literal["Covered", "Open"] | None = None
    transaction: Literal["Resale", "New Property"] = "Resale"
    furnishing: Literal["Furnished", "Semi-Furnished", "Unfurnished"] | None = None
    facing: (
        Literal["North", "South", "East", "West", "North - East", "North - West", "South - East", "South - West"] | None
    ) = None
    ownership: Literal["Freehold", "Leasehold", "Co-operative Society", "Power Of Attorney"] | None = None
    overlooking: list[Literal["Garden/Park", "Pool", "Main Road"]] = Field(default_factory=list, max_length=3)
    guideline_rate_sqft: float | None = Field(None, ge=100, le=500_000)  # ₹/sqft; anything below ₹100 is a typo
    loan_amount_inr: int | None = Field(None, gt=0, le=10**10)
    external_ref: str | None = Field(None, max_length=64)


class GoogleLogin(Strict):
    credential: str = Field(min_length=20, max_length=4096)  # the ID token from Google's sign-in button


class Register(Strict):
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=254)
    phone: str | None = Field(None, max_length=20)
    password: str = Field(min_length=8, max_length=200)


class ReviewAction(Strict):
    action: Literal["accept", "override", "site_visit"]
    override_value_inr: int | None = Field(None, gt=0)
    reason: str | None = Field(None, max_length=1000)


def _problem(status: int, detail, request: Request, headers=None):
    return JSONResponse(
        {"type": "about:blank", "title": "Error", "status": status, "detail": detail, "instance": request.url.path},
        status,
        headers=headers,
        media_type="application/problem+json",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    path = get_settings().model_dir / "model.joblib"
    # Only load model files produced by our own training job: joblib unpickles arbitrary code.
    app.state.model = joblib.load(path) if path.exists() else None
    yield


app = FastAPI(
    title="ValueGraph AI",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if get_settings().app_env == "prod" else "/docs",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["Authorization", "X-API-Key", "Idempotency-Key", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    cl = request.headers.get("content-length") or "0"
    if not cl.isdigit():
        return _problem(400, "Invalid Content-Length", request)
    if int(cl) > 64_000:
        return _problem(413, "Request body too large", request)
    resp = await call_next(request)
    resp.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            # Sends only our origin cross-site: Google Maps needs it to honour a referrer-restricted key.
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Cache-Control": "no-store",
        }
    )
    return resp


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return _problem(exc.status_code, exc.detail, request, getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return _problem(422, [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()], request)


def model(request: Request):
    m = request.app.state.model
    if m is None:
        raise HTTPException(503, "No trained model loaded; run `valuegraph train` first")
    return m


def _own_or_staff(v: Valuation | None, p: Principal) -> Valuation:
    if not v or (p.role not in (*STAFF, "admin") and v.user_id != p.user_id):
        raise HTTPException(404, "Valuation not found")  # 404, not 403: don't confirm the id exists
    return v


# ---- routes ------------------------------------------------------------------------------------
app.mount("/app", StaticFiles(directory=Path(__file__).parent.parent / "web", html=True), name="web")


SITE = Path(__file__).parent.parent / "site"
app.mount("/site", StaticFiles(directory=SITE), name="site")


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(SITE / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/config")
def client_config():
    # Browser key only — it is meant to ship to the client and is restricted by HTTP referrer.
    s = get_settings()
    return {"maps_api_key": s.maps_api_key, "google_client_id": s.google_client_id}


# ---- public (anonymous) endpoints for the marketing site ---------------------------------------
# Read-only and nothing is stored. Aggregated asking prices and a single-property preview carry no
# borrower data; lending decisions (routing, LTV, guideline checks) stay behind sign-in.
def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.get("/api/v1/public/cities")
def public_cities(request: Request, m=Depends(model)):
    throttle(f"public:{_client(request)}", 120)
    return sorted(m.localities)


@app.get("/api/v1/public/markets/{city}/localities")
def public_localities(city: str, request: Request, m=Depends(model)):
    throttle(f"public:{_client(request)}", 120)
    return _localities(city, m)


@app.get("/api/v1/public/markets/{city}/summary")
def public_summary(city: str, request: Request, m=Depends(model)):
    throttle(f"public:{_client(request)}", 120)
    return _summary(city, m)


@app.get("/api/v1/public/model")
def public_model(request: Request, m=Depends(model)):
    throttle(f"public:{_client(request)}", 120)
    t = (m.metrics or {}).get("results", {}).get("test", {}).get("valuegraph", {})
    return {
        "model_version": m.version,
        "cities": len(m.localities),
        "median_error": t.get("mdape"),
        "within_10pct": t.get("within_10pct"),
        "interval_coverage": t.get("interval_coverage"),
        "interval_level": m.level,
        "basis": m.ground_truth,
    }


@app.get("/api/v1/public/i18n")
def public_languages(request: Request):
    throttle(f"public:{_client(request)}", 120)
    return [{"code": c, "name": n} for c, n in i18n.LANGUAGES.items()]


@app.get("/api/v1/public/i18n/{lang}")
def public_strings(lang: str, request: Request):
    throttle(f"public:{_client(request)}", 120)
    if lang not in i18n.LANGUAGES:
        raise HTTPException(404, "Language not supported")
    s = get_settings()
    try:
        return i18n.get_strings(lang, s.i18n_cache_dir, "", s.gemini_model)  # cached or English: free
    except i18n.TranslationError:
        pass  # not cached yet: translate below
    if not s.gemini_api_key:
        raise HTTPException(503, "Translations aren't set up on this server (GEMINI_API_KEY)")
    throttle("gemini", s.gemini_max_calls_per_hour, window_s=3600)
    try:
        return i18n.get_strings(lang, s.i18n_cache_dir, s.gemini_api_key, s.gemini_model)
    except i18n.TranslationError as e:
        msg = str(e)
        if msg.startswith("rate_limit:"):
            raise HTTPException(503, "Translation quota exhausted — please try again later") from None
        raise HTTPException(502, f"Translation failed: {e}") from None


@app.post("/api/v1/public/estimate")
def public_estimate(body: ValuationIn, request: Request, m=Depends(model)):
    throttle(f"estimate:{_client(request)}", 20)  # model calls are the expensive part
    body = body.model_copy(update={"loan_amount_inr": None, "guideline_rate_sqft": None, "external_ref": None})
    result = _estimate(body, m)
    return {k: v for k, v in result.items() if k not in ("route", "lending", "guideline")}


@app.get("/ready")
def ready(request: Request):
    return {
        "status": "ready" if request.app.state.model else "degraded",
        "model_loaded": request.app.state.model is not None,
    }


@app.post("/api/v1/auth/token")
def login(body: Login, db=Depends(get_db)):
    email = body.email.strip().lower()
    throttle(f"login:{email}", 5, window_s=900)  # brute-force guard: 5 attempts / 15 min per account
    user = db.query(User).filter_by(email=email).one_or_none()
    if not check_password(user, body.password):
        raise HTTPException(401, "Invalid email or password")
    reset_throttle(f"login:{email}")
    audit(db, email, "login", user.id, {})
    db.commit()
    return {"access_token": create_token(user), "token_type": "bearer", "role": user.role}


@app.post("/api/v1/auth/register", status_code=201)
def register(body: Register, request: Request, db=Depends(get_db)):
    throttle(f"register:{_client(request)}", 10, window_s=900)  # 10 registrations / 15 min per IP
    email = body.email.strip().lower()
    if db.query(User).filter_by(email=email).one_or_none():
        raise HTTPException(409, "An account with this email already exists")
    s = get_settings()
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        role=s.google_default_role,  # same default as Google sign-in
        full_name=body.full_name.strip(),
        phone=body.phone.strip() if body.phone else None,
    )
    db.add(user)
    db.flush()
    audit(db, email, "user.create.register", user.id, {"role": user.role})
    db.commit()
    return {"access_token": create_token(user), "token_type": "bearer", "role": user.role, "email": email}


@app.post("/api/v1/auth/google")
def login_google(body: GoogleLogin, request: Request, db=Depends(get_db)):
    s = get_settings()
    if not s.google_client_id:
        raise HTTPException(404, "Google sign-in is not set up on this server")
    throttle(f"google:{_client(request)}", 10)
    try:
        claims = verify_google_id_token(body.credential, s.google_client_id)
    except (jwt.PyJWKClientError, jwt.InvalidTokenError):
        raise HTTPException(401, "Google sign-in failed. Try again.") from None
    email = claims["email"].strip().lower()
    user = db.query(User).filter_by(email=email).one_or_none()
    if user is None:
        # First Google sign-in creates the account. It has no usable password: Google is how it signs in.
        user = User(email=email, password_hash=hash_password(secrets.token_urlsafe(32)), role=s.google_default_role)
        db.add(user)
        db.flush()
        audit(db, email, "user.create.google", user.id, {"role": user.role})
    elif not user.is_active:
        raise HTTPException(403, "This account has been disabled")
    audit(db, email, "login.google", user.id, {})
    db.commit()
    return {"access_token": create_token(user), "token_type": "bearer", "role": user.role, "email": email}


@app.get("/api/v1/me")
def me(p: Principal = Depends(current_principal)):
    return {"email": p.email, "role": p.role, "via": p.via}


@app.post("/api/v1/valuations", status_code=201)
def create_valuation(
    body: ValuationIn,
    idempotency_key: str | None = Header(None, max_length=100),
    p: Principal = Depends(require("underwriter")),
    db=Depends(get_db),
    m=Depends(model),
):
    req_json = body.model_dump()
    req_hash = hashlib.sha256(json.dumps(req_json, sort_keys=True).encode()).hexdigest()
    if idempotency_key:
        prior = db.query(Valuation).filter_by(user_id=p.user_id, idempotency_key=idempotency_key).one_or_none()
        if prior:
            if prior.request_hash != req_hash:
                raise HTTPException(409, "Idempotency-Key reused with a different request")
            return prior.result

    result = _estimate(body, m)
    vid = f"VAL-{secrets.token_hex(6).upper()}"
    result = {"valuation_id": vid, "external_ref": body.external_ref, **result}
    created = now()
    result["created_at"] = created.isoformat()
    db.add(
        Valuation(
            id=vid,
            user_id=p.user_id,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
            request=req_json,
            result=result,
            reliability=result["reliability"],
            route=result["route"],
            model_version=result["model_version"],
            created_at=created,
        )
    )
    if result["route"] != "auto":
        db.add(Review(valuation_id=vid))
    audit(
        db,
        p.via + ":" + p.email,
        "valuation.create",
        vid,
        {
            "value": result["value"],
            "reliability": result["reliability"],
            "route": result["route"],
            "model_version": result["model_version"],
            "request_hash": req_hash,
        },
    )
    try:
        db.commit()  # commit before responding: dependency teardown runs after the response is sent
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Concurrent request with the same Idempotency-Key") from None
    return result


def _estimate(body: ValuationIn, m) -> dict:
    """Run the model for one property. Shared by the lender API and the public estimate."""
    if body.floor is not None and body.total_floors is not None and body.floor > body.total_floors:
        raise HTTPException(422, "floor can't be above total_floors")
    factor = LOADING[body.area_type]
    listing_sqft = body.area_sqft * factor
    attrs = body.model_dump(
        include={
            "area_type",
            "bathrooms",
            "balconies",
            "floor",
            "total_floors",
            "parking",
            "parking_type",
            "transaction",
            "furnishing",
            "facing",
            "ownership",
            "society",
        }
    )
    result = value_property(
        m,
        {
            **attrs,
            "city": body.city,
            "locality": body.locality,
            "sqft": listing_sqft,
            "bhk": body.bhk,
            "view_garden": int("Garden/Park" in body.overlooking),
            "view_pool": int("Pool" in body.overlooking),
            "view_main_road": int("Main Road" in body.overlooking),
            "guideline_rate_sqft": body.guideline_rate_sqft and body.guideline_rate_sqft / factor,
            "loan_amount_inr": body.loan_amount_inr,
        },
        get_settings(),
    )
    if result["rate_per_sqft"]:  # report ₹/sqft on the area basis the user gave
        result["rate_per_sqft"] = round(result["rate_per_sqft"] * factor)
    if result["guideline"]:
        result["guideline"]["rate_per_sqft"] = body.guideline_rate_sqft
    return {
        "area": {"value": body.area_sqft, "type": body.area_type, "listing_basis_sqft": round(listing_sqft)},
        **result,
    }


@app.get("/api/v1/valuations")
def list_valuations(
    limit: int = Query(50, ge=1, le=200), p: Principal = Depends(current_principal), db=Depends(get_db)
):
    q = db.query(Valuation).order_by(Valuation.created_at.desc())
    if p.role not in (*STAFF, "admin"):
        q = q.filter_by(user_id=p.user_id)
    return [
        {
            "valuation_id": v.id,
            "created_at": v.created_at.isoformat(),
            "city": v.request["city"],
            "value": v.result["value"],
            "reliability": v.reliability,
            "route": v.route,
        }
        for v in q.limit(limit)
    ]


@app.get("/api/v1/valuations/{vid}")
def get_valuation(vid: str, p: Principal = Depends(current_principal), db=Depends(get_db)):
    v = _own_or_staff(db.get(Valuation, vid), p)
    review = db.query(Review).filter_by(valuation_id=vid).one_or_none()
    return {
        **v.result,
        "review": review
        and {"status": review.status, "override_value_inr": review.override_value_inr, "reason": review.reason},
    }


@app.get("/api/v1/valuations/{vid}/audit")
def valuation_audit(vid: str, p: Principal = Depends(require("risk")), db=Depends(get_db)):
    rows = db.query(AuditLog).filter_by(entity_id=vid).order_by(AuditLog.id)
    return {
        "chain_valid": verify_audit_chain(db),
        "events": [
            {"at": r.at.isoformat(), "actor": r.actor, "action": r.action, "detail": r.detail, "hash": r.hash}
            for r in rows
        ],
    }


@app.get("/api/v1/markets/{city}/localities")
def localities(city: str, _: Principal = Depends(current_principal), m=Depends(model)):
    return _localities(city, m)


@app.get("/api/v1/markets/{city}/summary")
def market_summary(city: str, _: Principal = Depends(current_principal), m=Depends(model)):
    return _summary(city, m)


def _localities(city: str, m) -> list[dict]:
    counts = m.localities.get(city.strip().title())
    if counts is None:
        raise HTTPException(404, "City not covered")
    return [{"locality": k, "listings": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]


def _summary(city: str, m) -> dict:
    df: pd.DataFrame = m.comps.df
    d = df[df.city == city.strip().title()]
    if d.empty:
        raise HTTPException(404, "City not covered")
    loc = d.groupby("locality").rate.agg(["median", "size"]).query("size >= 5").sort_values("median", ascending=False)
    coords = m.comps.coords
    key = city.strip().title()
    return {
        "city": city.title(),
        "listings": len(d),
        "median_rate_per_sqft": round(d.rate.median()),
        "price_source": "asking",
        "localities": [
            {
                "locality": i,
                "median_rate_per_sqft": round(r["median"]),
                "listings": int(r["size"]),
                "lat": coords.get((key, i), (None, None))[0],
                "lon": coords.get((key, i), (None, None))[1],
            }
            for i, r in loc.iterrows()
        ],
    }


@app.get("/api/v1/models/status")
def model_status(_: Principal = Depends(current_principal), m=Depends(model)):
    return {
        "model_version": m.version,
        "ground_truth": m.ground_truth,
        "interval_level": m.level,
        "cities": sorted(m.localities),
        "evaluation": m.metrics,
    }


@app.get("/api/v1/reviews")
def list_reviews(status: str = "open", p: Principal = Depends(require(*STAFF)), db=Depends(get_db)):
    rows = (
        db.query(Review, Valuation)
        .join(Valuation, Review.valuation_id == Valuation.id)
        .filter(Review.status == status)
        .order_by(Valuation.created_at)
        .limit(200)
    )
    return [
        {
            "review_id": r.id,
            "valuation_id": v.id,
            "created_at": v.created_at.isoformat(),
            "city": v.request["city"],
            "value": v.result["value"],
            "reliability": v.reliability,
            "route": v.route,
            "reason_codes": v.result["reason_codes"],
            "status": r.status,
        }
        for r, v in rows
    ]


@app.patch("/api/v1/reviews/{rid}")
def act_on_review(rid: int, body: ReviewAction, p: Principal = Depends(require("reviewer")), db=Depends(get_db)):
    r = db.get(Review, rid)
    if not r:
        raise HTTPException(404, "Review not found")
    if r.status != "open":
        raise HTTPException(409, f"Review already {r.status}")
    if body.action == "override" and (not body.override_value_inr or not (body.reason or "").strip()):
        raise HTTPException(422, "Override needs override_value_inr and a reason")
    r.status = {"accept": "accepted", "override": "overridden", "site_visit": "site_visit"}[body.action]
    r.reviewer_id, r.override_value_inr, r.reason = p.user_id, body.override_value_inr, body.reason
    audit(db, p.via + ":" + p.email, f"review.{body.action}", r.valuation_id, body.model_dump())
    db.commit()
    return {"review_id": r.id, "status": r.status}
