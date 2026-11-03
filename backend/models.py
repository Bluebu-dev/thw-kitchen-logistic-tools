"""Persistent application records. Decimal stock and append-only history are deliberate."""

import os
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc)


engine = create_engine(
    os.getenv("DATABASE_URL", "sqlite:///./dev.db"),
    pool_pre_ping=True,
    connect_args={"check_same_thread": False}
    if os.getenv("DATABASE_URL", "sqlite:").startswith("sqlite")
    else {},
)
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def sqlite_setup(conn, record):
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")


SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="user")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(100))


class Setting(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    ov_name: Mapped[str] = mapped_column(String(150), default="Ortsverband Passau")
    logo: Mapped[bytes | None] = mapped_column(LargeBinary)
    logo_type: Mapped[str] = mapped_column(String(30), default="image/png")


class EventTemplate(Base):
    __tablename__ = "event_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    categories: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    type: Mapped[str] = mapped_column(String(20), default="Einsatz")
    date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    people: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(180), default="")
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    doc: Mapped[dict] = mapped_column(JSON, default=dict)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    template_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    retention_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )


class OperationItem(Base):
    __tablename__ = "operation_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    category: Mapped[str] = mapped_column(String(150))
    label: Mapped[str] = mapped_column(String(200))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    quantity: Mapped[str] = mapped_column(String(100), default="")
    note: Mapped[str] = mapped_column(String(500), default="")
    custom: Mapped[bool] = mapped_column(Boolean, default=False)


class StockArticle(Base):
    __tablename__ = "stock_articles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(20), default="Lebensmittel")
    unit: Mapped[str] = mapped_column(String(10))
    target: Mapped[Decimal] = mapped_column(Numeric(15, 3), default=0)
    balance: Mapped[Decimal] = mapped_column(Numeric(15, 3), default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (UniqueConstraint("actor_id", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("stock_articles.id"))
    delta: Mapped[Decimal] = mapped_column(Numeric(15, 3))
    before: Mapped[Decimal] = mapped_column(Numeric(15, 3))
    after: Mapped[Decimal] = mapped_column(Numeric(15, 3))
    reason: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(String(500))
    # Stable historical reference; deliberately not a cascading FK. Later lots attach to movements.
    event_id: Mapped[int | None] = mapped_column(Integer)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor: Mapped[str] = mapped_column(String(80))
    external_id: Mapped[str | None] = mapped_column(String(100))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    location: Mapped[str] = mapped_column(String(180))
    kind: Mapped[str] = mapped_column(String(20), default="Kühllager")
    lower: Mapped[Decimal] = mapped_column(Numeric(7, 3))
    upper: Mapped[Decimal] = mapped_column(Numeric(7, 3))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (UniqueConstraint("actor_id", "device_id", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    value: Mapped[Decimal] = mapped_column(Numeric(7, 3))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lower: Mapped[Decimal] = mapped_column(Numeric(7, 3))
    upper: Mapped[Decimal] = mapped_column(Numeric(7, 3))
    device_name: Mapped[str] = mapped_column(String(150))
    device_location: Mapped[str] = mapped_column(String(180))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[int | None] = mapped_column(Integer)
    external_id: Mapped[str | None] = mapped_column(String(100))


class ReadingNote(Base):
    __tablename__ = "reading_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    reading_id: Mapped[int] = mapped_column(ForeignKey("readings.id"))
    text: Mapped[str] = mapped_column(String(500))
    actor: Mapped[str] = mapped_column(String(80))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSON)
    device_ids: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id"))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("event_templates.id"))
    actor: Mapped[str] = mapped_column(String(80))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FormTemplate(Base):
    __tablename__ = "form_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(100))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    layout: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FormRecord(Base):
    __tablename__ = "form_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("form_templates.id"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id"))
    title: Mapped[str] = mapped_column(String(200))
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    extra_pages: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    correction_of: Mapped[int | None] = mapped_column(Integer)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by: Mapped[str | None] = mapped_column(String(80))
    pdf: Mapped[bytes | None] = mapped_column(LargeBinary)
    pdf_hash: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(80))
