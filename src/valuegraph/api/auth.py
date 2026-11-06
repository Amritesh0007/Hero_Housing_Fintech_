import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header, HTTPException

from ..config import get_settings
from .db import ApiKey, User, get_db

_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("timing-equaliser")
ISSUER = "valuegraph"


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    role: str
    via: str  # "jwt" | "api_key:<prefix>"


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def check_password(user: User | None, pw: str) -> bool:
    try:
        # Verify against a dummy hash for unknown users so response time doesn't reveal which emails exist.
        return _ph.verify(user.password_hash if user else _DUMMY_HASH, pw) and user is not None and user.is_active
    except VerifyMismatchError:
        return False


def create_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": user.id,
        "role": user.role,
        "iss": ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=s.jwt_ttl_min),
    }
    return jwt.encode(claims, s.jwt_secret, algorithm="HS256")


# Google's public signing keys; PyJWKClient caches them so we don't fetch on every login.
_google_keys = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", cache_keys=True, lifespan=3600)


def verify_google_id_token(token: str, client_id: str) -> dict:
    """Check a 'Sign in with Google' ID token and return its claims.

    Verifies Google's RSA signature, that it was issued for *our* client ID (aud), by Google (iss),
    hasn't expired, and that Google has verified the email. Raises jwt.PyJWKClientError /
    jwt.InvalidTokenError otherwise."""
    key = _google_keys.get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=["https://accounts.google.com", "accounts.google.com"],
        options={"require": ["exp", "iat", "aud", "iss", "sub", "email"]},
    )
    if claims.get("email_verified") is not True:
        raise jwt.InvalidTokenError("Google has not verified this email")
    return claims


def new_api_key() -> tuple[str, str, str]:
    """Returns (raw key shown once, prefix, sha256 of secret). Only prefix + hash are stored."""
    prefix, secret = secrets.token_hex(4), secrets.token_urlsafe(32)
    return f"vg_{prefix}_{secret}", prefix, hashlib.sha256(secret.encode()).hexdigest()


# shortcut: per-process memory; with >1 API replica move these counters to Redis.
_hits: dict[str, deque] = defaultdict(deque)


def throttle(key: str, limit: int, window_s: float = 60.0):
    if len(_hits) > 50_000:  # keys include attacker-chosen emails; drop idle ones so memory stays bounded
        cutoff = time.monotonic() - 900
        for k in [k for k, v in _hits.items() if not v or v[-1] < cutoff]:
            del _hits[k]
    q, t = _hits[key], time.monotonic()
    while q and t - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests", headers={"Retry-After": str(int(window_s))})
    q.append(t)


def reset_throttle(key: str):
    _hits.pop(key, None)


def current_principal(
    authorization: str | None = Header(None), x_api_key: str | None = Header(None), db=Depends(get_db)
) -> Principal:
    unauthorized = HTTPException(401, "Invalid or missing credentials", headers={"WWW-Authenticate": "Bearer"})
    if x_api_key:
        parts = x_api_key.split("_", 2)
        if len(parts) != 3 or parts[0] != "vg":
            raise unauthorized
        key = db.query(ApiKey).filter_by(prefix=parts[1]).one_or_none()
        digest = hashlib.sha256(parts[2].encode()).hexdigest()
        if not key or key.revoked_at or not hmac.compare_digest(key.key_hash, digest):
            raise unauthorized
        user, via = db.get(User, key.user_id), f"api_key:{key.prefix}"
    elif authorization and authorization.lower().startswith("bearer "):
        try:
            claims = jwt.decode(
                authorization[7:],
                get_settings().jwt_secret,
                algorithms=["HS256"],
                issuer=ISSUER,
                options={"require": ["exp", "sub", "iss"]},
            )
        except jwt.PyJWTError:
            raise unauthorized from None
        user, via = db.get(User, claims["sub"]), "jwt"
    else:
        raise unauthorized
    if not user or not user.is_active:
        raise unauthorized
    p = Principal(user.id, user.email, user.role, via)
    throttle(f"p:{p.user_id}", get_settings().rate_limit_per_min)
    return p


def require(*roles: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.role not in roles and p.role != "admin":
            raise HTTPException(403, "Insufficient role")
        return p

    return dep
