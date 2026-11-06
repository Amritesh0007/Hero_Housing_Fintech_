import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from ..config import get_settings

_engine = None
_Session = None


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "app_user"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ApiKey(Base):
    __tablename__ = "api_key"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Valuation(Base):
    """A valuation is an immutable document: request + full result stored as JSON for exact replay."""

    __tablename__ = "valuation"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    reliability: Mapped[str] = mapped_column(String(30), index=True)
    route: Mapped[str] = mapped_column(String(30))
    model_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Review(Base):
    __tablename__ = "review"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    valuation_id: Mapped[str] = mapped_column(ForeignKey("valuation.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open|accepted|overridden|site_visit
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    override_value_inr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AuditLog(Base):
    """Append-only, hash-chained. Altering any row breaks every hash after it."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(50), index=True)
    detail: Mapped[dict] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


def _chain_hash(prev: str, actor: str, action: str, entity_id: str, detail: dict) -> str:
    payload = json.dumps([prev, actor, action, entity_id, detail], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def audit(db, actor: str, action: str, entity_id: str, detail: dict):
    # shortcut: reads the last hash without a lock; under concurrent writers on Postgres wrap this in
    # pg_advisory_xact_lock so the chain stays linear.
    last = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    prev = last.hash if last else "0" * 64
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_id=entity_id,
            detail=detail,
            prev_hash=prev,
            hash=_chain_hash(prev, actor, action, entity_id, detail),
        )
    )
    db.flush()


def verify_audit_chain(db) -> bool:
    prev = "0" * 64
    for row in db.query(AuditLog).order_by(AuditLog.id):
        if row.prev_hash != prev or row.hash != _chain_hash(prev, row.actor, row.action, row.entity_id, row.detail):
            return False
        prev = row.hash
    return True


def init_db():
    global _engine, _Session
    url = get_settings().database_url
    _engine = create_engine(
        url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {}, pool_pre_ping=True
    )
    _Session = sessionmaker(_engine, expire_on_commit=False)
    # shortcut: create_all instead of migrations; add Alembic before the first schema change in a live DB.
    Base.metadata.create_all(_engine)


@contextmanager
def session_scope():
    db = _Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    with session_scope() as db:
        yield db
