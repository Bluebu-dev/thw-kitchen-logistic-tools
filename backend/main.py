"""THW kitchen logistics: one OV per installation, session UI and scoped machine API."""

import asyncio
import calendar
import hashlib
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import fitz
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, func, update, delete, inspect, text
from sqlalchemy.exc import IntegrityError

from .models import (
    ApiKey,
    Audit,
    Base,
    Device,
    Document,
    EventTemplate,
    FormRecord,
    FormTemplate,
    LoginSession,
    Operation,
    OperationItem,
    Reading,
    ReadingNote,
    Setting,
    StockArticle,
    StockMovement,
    User,
    SessionLocal,
    engine,
    now,
)
from .schemas import (
    ArticleInput,
    Credentials,
    DeviceInput,
    ExportInput,
    FinalizeInput,
    FormInput,
    FormPatch,
    ItemInput,
    ItemPatch,
    KeyInput,
    MovementInput,
    NoteInput,
    ReadingInput,
    OperationInput,
    OperationPatch,
    SettingsInput,
    TemplateInput,
    UserCreate,
    UserUpdate,
)
from . import pdf_forms

ph = PasswordHasher()
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
KITCHEN = {"admin", "staff", "cook"}
LEAD = {"admin", "staff"}


def stamp(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def utc(value):
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def db():
    with SessionLocal() as s:
        yield s


def public(obj, exclude=()):
    return {
        c.name: (
            str(getattr(obj, c.name))
            if isinstance(getattr(obj, c.name), Decimal)
            else utc(getattr(obj, c.name)).isoformat()
            if isinstance(getattr(obj, c.name), datetime)
            else getattr(obj, c.name)
        )
        for c in obj.__table__.columns
        if c.name not in exclude
    }


def user_public(u):
    return public(u, ("password_hash",))


def audit(s, u, action, entity):
    s.add(Audit(actor=u.username, action=action, entity=str(entity)))


def get(s, model, id):
    obj = s.get(model, id)
    if obj is None:
        raise HTTPException(404, "Eintrag nicht gefunden")
    return obj


def role(u, roles):
    if u.role not in roles:
        raise HTTPException(403, "Keine Berechtigung für diese Aktion")
    return u


def auth(request: Request, s=Depends(db)):
    raw = request.cookies.get("thw_session", "")
    sess = (
        s.scalar(select(LoginSession).where(LoginSession.token_hash == stamp(raw)))
        if raw
        else None
    )
    if not sess or utc(sess.expires) <= now():
        raise HTTPException(401, "Anmeldung erforderlich")
    u = get(s, User, sess.user_id)
    if not u.active or u.role == "technical":
        raise HTTPException(401, "Benutzer deaktiviert oder nur für API vorgesehen")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-CSRF-Token") != sess.csrf:
            raise HTTPException(403, "CSRF-Prüfung fehlgeschlagen")
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Ungültiger Origin")
    return u


def admin(u=Depends(auth)):
    return role(u, {"admin"})


def kitchen(u=Depends(auth)):
    return role(u, KITCHEN)


def lead(u=Depends(auth)):
    return role(u, LEAD)


def machine(request: Request, s=Depends(db)):
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "API-Schlüssel erforderlich")
    key = s.scalar(
        select(ApiKey).where(
            ApiKey.token_hash == stamp(header[7:]), ApiKey.active == True
        )
    )
    if not key:
        raise HTTPException(401, "API-Schlüssel ungültig")
    u = get(s, User, key.user_id)
    if not u.active or u.role != "technical":
        raise HTTPException(403, "Technischer Benutzer deaktiviert")
    return (u, key)


def scope(identity, required, device=None):
    u, key = identity
    if required not in key.scopes:
        raise HTTPException(403, "API-Schlüssel hat dieses Recht nicht")
    if device is not None and key.device_ids and device not in key.device_ids:
        raise HTTPException(403, "Gerät nicht freigegeben")
    return u


def event_access(s, id, u, write=False):
    stmt = select(Operation).where(Operation.id == id)
    if write:
        stmt = stmt.with_for_update()
    e = s.scalar(stmt)
    if not e or (e.deleted_at and u.role != "admin"):
        raise HTTPException(404, "Vorgang nicht gefunden")
    if e.deleted_at and write:
        raise HTTPException(409, "Archivierte Vorgänge sind schreibgeschützt")
    return e


def install_guards():
    """Backstop application checks: even accidental ORM updates cannot rewrite proofs."""
    with engine.begin() as c:
        if engine.dialect.name == "sqlite":
            for table in ("stock_movements", "readings", "reading_notes"):
                for operation in ("UPDATE", "DELETE"):
                    c.exec_driver_sql(
                        f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'append-only history'); END"
                    )
            c.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS final_form_update BEFORE UPDATE ON form_records WHEN OLD.finalized_at IS NOT NULL BEGIN SELECT RAISE(ABORT,'finalized proof'); END"
            )
            c.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS final_form_delete BEFORE DELETE ON form_records WHEN OLD.finalized_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM operations WHERE id=OLD.event_id AND deleted_at IS NOT NULL AND retention_until<=CURRENT_TIMESTAMP) BEGIN SELECT RAISE(ABORT,'finalized proof'); END"
            )
            c.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS template_content_update BEFORE UPDATE ON form_templates WHEN NEW.content != OLD.content OR NEW.layout != OLD.layout BEGIN SELECT RAISE(ABORT,'immutable template version'); END"
            )
        elif engine.dialect.name == "postgresql":
            c.exec_driver_sql(
                """CREATE OR REPLACE FUNCTION kitchen_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append-only history'; END $$"""
            )
            for table in ("stock_movements", "readings", "reading_notes"):
                c.exec_driver_sql(
                    f"DROP TRIGGER IF EXISTS immutable_history ON {table}"
                )
                c.exec_driver_sql(
                    f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION kitchen_append_only()"
                )
            c.exec_driver_sql("""CREATE OR REPLACE FUNCTION kitchen_final_guard() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF OLD.finalized_at IS NOT NULL THEN
                IF TG_OP='DELETE' AND EXISTS (SELECT 1 FROM operations WHERE id=OLD.event_id AND deleted_at IS NOT NULL AND retention_until<=CURRENT_TIMESTAMP) THEN RETURN OLD; END IF;
                RAISE EXCEPTION 'finalized proof';
              END IF;
              IF TG_OP='DELETE' THEN RETURN OLD; END IF;
              RETURN NEW;
            END $$""")
            c.exec_driver_sql("DROP TRIGGER IF EXISTS final_guard ON form_records")
            c.exec_driver_sql(
                "CREATE TRIGGER final_guard BEFORE UPDATE OR DELETE ON form_records FOR EACH ROW EXECUTE FUNCTION kitchen_final_guard()"
            )
            c.exec_driver_sql(
                """CREATE OR REPLACE FUNCTION kitchen_template_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.content<>OLD.content OR NEW.layout::text<>OLD.layout::text THEN RAISE EXCEPTION 'immutable template version'; END IF; RETURN NEW; END $$"""
            )
            c.exec_driver_sql("DROP TRIGGER IF EXISTS template_guard ON form_templates")
            c.exec_driver_sql(
                "CREATE TRIGGER template_guard BEFORE UPDATE ON form_templates FOR EACH ROW EXECUTE FUNCTION kitchen_template_guard()"
            )


def purge_expired():
    with SessionLocal() as s:
        due = s.scalars(
            select(Operation)
            .where(
                Operation.deleted_at.is_not(None), Operation.retention_until <= now()
            )
            .with_for_update()
        ).all()
        for e in due:
            for model in (Document, FormRecord, OperationItem):
                s.execute(delete(model).where(model.event_id == e.id))
            s.add(Audit(actor="system", action="retention.purge", entity=str(e.id)))
            s.delete(e)
        s.commit()
        return len(due)


def initialize():
    # Version 1 is intentionally not silently rewritten: user requested a fresh installation.
    if "events" in inspect(engine).get_table_names():
        raise RuntimeError(
            "Version 2 benötigt eine neue Datenbank. Bestehende Version-1-Datenbank unverändert sichern und DATABASE_URL auf eine leere Datenbank setzen; siehe README."
        )
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        if not s.scalar(select(User.id).limit(1)):
            password = os.getenv("ADMIN_PASSWORD", "")
            if len(password) < 12:
                raise RuntimeError("ADMIN_PASSWORD muss mindestens 12 Zeichen haben")
            s.add(
                User(
                    username=os.getenv("ADMIN_USERNAME", "admin"),
                    password_hash=ph.hash(password),
                    role="admin",
                )
            )
        if not s.get(Setting, 1):
            s.add(Setting(id=1, ov_name=os.getenv("OV_NAME", "Ortsverband Passau")))
        if not s.scalar(select(EventTemplate.id).limit(1)):
            rows = json.loads((Path(__file__).parent / "defaults.json").read_text())
            s.add(
                EventTemplate(
                    name="Standardausstattung",
                    categories=[{"name": name, "items": items} for name, items in rows],
                )
            )
        if not s.scalar(select(FormTemplate.id).limit(1)):
            names = {
                "02": "Lebensmittelhygiene OV-Küche",
                "03": "Lebensmittelhygiene Einsatz",
                "04": "Wareneingangskontrolle",
                "05": "Hygienekontrolle Lagerraum",
                "06": "Temperaturkontrolle Tiefkühllager",
                "07": "Temperaturkontrolle Kühllager",
                "08": "Kerntemperaturen Erhitzung",
                "09": "Temperaturkontrolle Speisenausgabe",
            }
            for p in sorted(pdf_forms.FORM_DIR.glob("*.pdf")):
                s.add(
                    FormTemplate(
                        code=p.stem,
                        name=names[p.stem[-2:]],
                        version="April 2020",
                        content=p.read_bytes(),
                        layout=json.loads(p.with_suffix(".json").read_text()),
                    )
                )
        s.commit()
    install_guards()
    purge_expired()


async def maintenance():
    while True:
        await asyncio.sleep(3600)
        try:
            await asyncio.to_thread(purge_expired)
        except Exception:
            logging.getLogger(__name__).exception(
                "Aufbewahrungsbereinigung fehlgeschlagen; nächster Versuch in einer Stunde"
            )


@asynccontextmanager
async def lifespan(app):
    initialize()
    task = asyncio.create_task(maintenance())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="THW Küchenlogistik",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.post("/api/auth/login")
def login(data: Credentials, response: Response, s=Depends(db)):
    u = s.scalar(select(User).where(User.username == data.username))
    try:
        valid = bool(u and ph.verify(u.password_hash, data.password))
    except VerificationError:
        valid = False
    if not valid or not u.active or u.role == "technical":
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    raw = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    s.add(
        LoginSession(
            token_hash=stamp(raw),
            csrf=csrf,
            user_id=u.id,
            expires=now() + timedelta(days=7),
        )
    )
    audit(s, u, "login", "session")
    s.commit()
    response.set_cookie(
        "thw_session",
        raw,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=604800,
        path="/",
    )
    return {"user": user_public(u), "csrf": csrf}


@app.get("/api/auth/me")
def me(request: Request, u=Depends(auth), s=Depends(db)):
    sess = s.scalar(
        select(LoginSession).where(
            LoginSession.token_hash == stamp(request.cookies["thw_session"])
        )
    )
    return {"user": user_public(u), "csrf": sess.csrf}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, u=Depends(auth), s=Depends(db)):
    s.execute(
        delete(LoginSession).where(
            LoginSession.token_hash == stamp(request.cookies["thw_session"])
        )
    )
    s.commit()
    response.delete_cookie("thw_session", path="/")
    return {"ok": True}


@app.get("/api/users")
def users(u=Depends(admin), s=Depends(db)):
    return [user_public(x) for x in s.scalars(select(User).order_by(User.username))]


@app.post("/api/users", status_code=201)
def create_user(data: UserCreate, u=Depends(admin), s=Depends(db)):
    if len(data.password) < 12:
        raise HTTPException(422, "Passwort muss mindestens 12 Zeichen haben")
    if s.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(409, "Benutzer existiert bereits")
    x = User(
        username=data.username, password_hash=ph.hash(data.password), role=data.role
    )
    s.add(x)
    audit(s, u, "user.create", data.username)
    s.commit()
    return user_public(x)


@app.patch("/api/users/{id}")
def change_user(id: int, data: UserUpdate, u=Depends(admin), s=Depends(db)):
    all_users = s.scalars(select(User).order_by(User.id).with_for_update()).all()
    x = next((x for x in all_users if x.id == id), None)
    if not x:
        raise HTTPException(404, "Benutzer nicht gefunden")
    values = data.model_dump(exclude_unset=True)
    if any(v is None for v in values.values()):
        raise HTTPException(422, "Leere Änderungen sind nicht erlaubt")
    demotes = values.get("active") is False or (
        "role" in values and values["role"] != "admin"
    )
    if (
        x.role == "admin"
        and demotes
        and (
            x.id == u.id or sum(a.active and a.role == "admin" for a in all_users) <= 1
        )
    ):
        raise HTTPException(409, "Eigenen oder letzten Adminzugriff nicht entfernen")
    if "password" in values:
        values["password_hash"] = ph.hash(values.pop("password"))
    for k, v in values.items():
        setattr(x, k, v)
    s.execute(delete(LoginSession).where(LoginSession.user_id == id))
    audit(s, u, "user.update", x.username)
    s.commit()
    return user_public(x)


@app.get("/api/settings")
def settings(s=Depends(db)):
    x = get(s, Setting, 1)
    return {"ov_name": x.ov_name, "logo_url": "/api/settings/logo"}


@app.put("/api/settings")
def change_settings(data: SettingsInput, u=Depends(admin), s=Depends(db)):
    x = get(s, Setting, 1)
    x.ov_name = data.ov_name
    audit(s, u, "settings.update", "OV")
    s.commit()
    return {"ov_name": x.ov_name}


@app.get("/api/settings/logo")
def logo(s=Depends(db)):
    x = get(s, Setting, 1)
    if x.logo:
        return Response(
            x.logo,
            media_type=x.logo_type,
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )
    return FileResponse(Path(__file__).parent.parent / "frontend/thw-logo.png")


@app.put("/api/settings/logo")
async def upload_logo(file: UploadFile = File(...), u=Depends(admin), s=Depends(db)):
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "Logo maximal 5 MB")
    try:
        image = Image.open(BytesIO(raw))
        if image.width * image.height > 16000000:
            raise ValueError()
        image.load()
        image = image.convert("RGBA")
        image.thumbnail((2000, 2000))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise HTTPException(422, "PNG- oder JPEG-Logo erforderlich")
    x = get(s, Setting, 1)
    x.logo = buffer.getvalue()
    x.logo_type = "image/png"
    audit(s, u, "settings.logo", "OV")
    s.commit()
    return {"ok": True}


@app.get("/api/event-templates")
def event_templates(u=Depends(auth), s=Depends(db)):
    return [
        public(x) for x in s.scalars(select(EventTemplate).order_by(EventTemplate.id))
    ]


@app.post("/api/event-templates", status_code=201)
def create_template(data: TemplateInput, u=Depends(kitchen), s=Depends(db)):
    x = EventTemplate(**data.model_dump())
    s.add(x)
    s.flush()
    audit(s, u, "template.create", x.id)
    s.commit()
    return public(x)


@app.put("/api/event-templates/{id}")
def change_template(id: int, data: TemplateInput, u=Depends(kitchen), s=Depends(db)):
    x = get(s, EventTemplate, id)
    for k, v in data.model_dump().items():
        setattr(x, k, v)
    audit(s, u, "template.update", id)
    s.commit()
    return public(x)


def event_public(s, e, detail=False):
    result = public(e)
    items = s.scalars(
        select(OperationItem)
        .where(OperationItem.event_id == e.id)
        .order_by(OperationItem.id)
    ).all()
    result.update(item_count=len(items), done_count=sum(i.done for i in items))
    if detail:
        result["items"] = [public(i) for i in items]
    return result


@app.get("/api/events")
def events(archived: bool = False, u=Depends(auth), s=Depends(db)):
    if archived:
        role(u, {"admin"})
    stmt = (
        select(Operation)
        .where(
            Operation.deleted_at.is_not(None)
            if archived
            else Operation.deleted_at.is_(None)
        )
        .order_by(Operation.date.desc(), Operation.id.desc())
    )
    return [event_public(s, e) for e in s.scalars(stmt)]


@app.post("/api/events", status_code=201)
def create_event(data: OperationInput, u=Depends(auth), s=Depends(db)):
    attrs = data.model_dump(exclude={"template_ids", "date", "end_date"})
    templates = []
    for id in dict.fromkeys(data.template_ids):
        t = get(s, EventTemplate, id)
        if not t.active:
            raise HTTPException(422, "Inaktive Vorlage")
        templates.append(t)
    e = Operation(
        **attrs,
        date=data.date.isoformat(),
        end_date=(data.end_date or data.date).isoformat(),
        created_by=u.id,
        template_ids=[t.id for t in templates],
        categories=[],
    )
    s.add(e)
    s.flush()
    cats = []
    for t in templates:
        for c in t.categories:
            if c["name"] not in cats:
                cats.append(c["name"])
            for label in c["items"]:
                s.add(OperationItem(event_id=e.id, category=c["name"], label=label))
        for d in s.scalars(select(Document).where(Document.template_id == t.id)):
            s.add(
                Document(
                    name=d.name,
                    content=d.content,
                    size=d.size,
                    event_id=e.id,
                    actor=u.username,
                )
            )
    e.categories = cats
    audit(s, u, "event.create", e.id)
    s.commit()
    return event_public(s, e, True)


@app.get("/api/events/{id}")
def event_detail(id: int, u=Depends(auth), s=Depends(db)):
    return event_public(s, event_access(s, id, u), True)


@app.patch("/api/events/{id}")
def patch_event(id: int, data: OperationPatch, u=Depends(kitchen), s=Depends(db)):
    e = event_access(s, id, u, True)
    attrs = data.model_dump(exclude_unset=True)
    if any(v is None for v in attrs.values()):
        raise HTTPException(422, "Leere Werte sind nicht erlaubt")
    for k, v in attrs.items():
        setattr(e, k, v.isoformat() if k in ("date", "end_date") else v)
    if e.end_date < e.date:
        raise HTTPException(422, "Enddatum liegt vor Beginn")
    audit(s, u, "event.update", id)
    s.commit()
    return event_public(s, e, True)


def ten_years_after(value):
    return value.replace(
        year=value.year + 10,
        day=min(value.day, calendar.monthrange(value.year + 10, value.month)[1]),
    )


@app.delete("/api/events/{id}")
def archive_event(id: int, u=Depends(admin), s=Depends(db)):
    e = event_access(s, id, u, True)
    latest = s.scalar(
        select(func.max(FormRecord.finalized_at)).where(FormRecord.event_id == id)
    )
    e.deleted_at = now()
    e.retention_until = ten_years_after(
        max(e.deleted_at, utc(latest) if latest else e.deleted_at)
    )
    audit(s, u, "event.archive", id)
    s.commit()
    return event_public(s, e)


@app.post("/api/events/{id}/categories")
def add_category(id: int, data: SettingsInput, u=Depends(kitchen), s=Depends(db)):
    e = event_access(s, id, u, True)
    if data.ov_name in e.categories:
        raise HTTPException(409, "Kategorie existiert bereits")
    e.categories = [*e.categories, data.ov_name]
    audit(s, u, "event.category", id)
    s.commit()
    return e.categories


@app.post("/api/events/{id}/items", status_code=201)
def add_item(id: int, data: ItemInput, u=Depends(kitchen), s=Depends(db)):
    e = event_access(s, id, u, True)
    if data.category not in e.categories:
        e.categories = [*e.categories, data.category]
    i = OperationItem(event_id=id, **data.model_dump(), custom=True)
    s.add(i)
    s.flush()
    audit(s, u, "item.create", i.id)
    s.commit()
    return public(i)


@app.patch("/api/events/{id}/items/{item_id}")
def change_item(id: int, item_id: int, data: ItemPatch, u=Depends(auth), s=Depends(db)):
    event_access(s, id, u, True)
    i = get(s, OperationItem, item_id)
    if i.event_id != id:
        raise HTTPException(404, "Prüfpunkt nicht gefunden")
    attrs = data.model_dump(exclude_unset=True)
    if any(v is None for v in attrs.values()):
        raise HTTPException(422, "Leere Werte sind nicht erlaubt")
    for k, v in attrs.items():
        setattr(i, k, v)
    audit(s, u, "item.update", item_id)
    s.commit()
    return public(i)


@app.delete("/api/events/{id}/items/{item_id}")
def remove_item(id: int, item_id: int, u=Depends(kitchen), s=Depends(db)):
    event_access(s, id, u, True)
    i = get(s, OperationItem, item_id)
    if i.event_id != id:
        raise HTTPException(404, "Prüfpunkt nicht gefunden")
    s.delete(i)
    audit(s, u, "item.remove", item_id)
    s.commit()
    return {"ok": True}


@app.put("/api/events/{id}/documentation")
def documentation(id: int, data: dict[str, str], u=Depends(kitchen), s=Depends(db)):
    if len(data) > 30 or any(len(k) > 100 or len(v) > 10000 for k, v in data.items()):
        raise HTTPException(422, "Dokumentation zu lang")
    e = event_access(s, id, u, True)
    e.doc = data
    audit(s, u, "event.documentation", id)
    s.commit()
    return e.doc


# Decimal inventory: movements form the immutable ledger, balance is its transactional projection.
def article_public(x):
    result = public(x)
    result.update(below_target=x.balance < x.target, negative=x.balance < 0)
    return result


@app.get("/api/stock/articles")
def articles(u=Depends(auth), s=Depends(db)):
    return [
        article_public(x)
        for x in s.scalars(select(StockArticle).order_by(StockArticle.name))
    ]


def validate_unit(unit, quantity):
    if unit == "Stück" and quantity != quantity.to_integral_value():
        raise HTTPException(422, "Stück müssen ganzzahlig sein")


@app.post("/api/stock/articles", status_code=201)
def new_article(data: ArticleInput, u=Depends(kitchen), s=Depends(db)):
    validate_unit(data.unit, data.target)
    x = StockArticle(**data.model_dump())
    s.add(x)
    s.flush()
    audit(s, u, "stock.article", x.id)
    s.commit()
    return article_public(x)


@app.put("/api/stock/articles/{id}")
def edit_article(id: int, data: ArticleInput, u=Depends(kitchen), s=Depends(db)):
    x = s.scalar(select(StockArticle).where(StockArticle.id == id).with_for_update())
    if not x:
        raise HTTPException(404, "Artikel nicht gefunden")
    validate_unit(data.unit, data.target)
    if data.unit != x.unit and s.scalar(
        select(StockMovement.id).where(StockMovement.article_id == id).limit(1)
    ):
        raise HTTPException(409, "Einheit nach erster Buchung nicht mehr änderbar")
    for k, v in data.model_dump().items():
        setattr(x, k, v)
    audit(s, u, "stock.article.update", id)
    s.commit()
    return article_public(x)


def movement_response(x):
    return public(x)


def book_movement(data, u, s):
    if data.direction == "inventory":
        role(u, LEAD)
        if data.reason != "Inventurkorrektur":
            raise HTTPException(422, "Inventur benötigt den Grund Inventurkorrektur")
    elif data.reason == "Inventurkorrektur":
        raise HTTPException(422, "Inventurkorrektur benötigt Ist-Bestand")
    if data.external_id:
        old = s.scalar(
            select(StockMovement).where(
                StockMovement.actor_id == u.id,
                StockMovement.external_id == data.external_id,
            )
        )
        if old:
            requested = old.after if data.direction == "inventory" else abs(old.delta)
            if (
                old.article_id != data.article_id
                or requested != data.quantity
                or (data.direction == "out" and old.delta >= 0)
                or (data.direction == "in" and old.delta <= 0)
                or old.reason != data.reason
                or old.note != data.note
                or old.event_id != data.event_id
            ):
                raise HTTPException(
                    409, "Übertragungs-ID bereits mit anderen Daten verwendet"
                )
            return movement_response(old)
    if data.event_id is not None:
        event_access(s, data.event_id, u, True)
    x = s.scalar(
        select(StockArticle).where(StockArticle.id == data.article_id).with_for_update()
    )
    if not x or not x.active:
        raise HTTPException(404, "Aktiver Artikel nicht gefunden")
    validate_unit(x.unit, data.quantity)
    if data.direction != "inventory" and data.quantity == 0:
        raise HTTPException(422, "Buchungsmenge muss größer als null sein")
    before = x.balance
    delta = (
        data.quantity - before
        if data.direction == "inventory"
        else data.quantity * (1 if data.direction == "in" else -1)
    )
    # Atomic compare-and-set also protects SQLite and detects concurrent inventory adjustments.
    if before + delta < 0:
        raise HTTPException(
            409, "Entnahme abgelehnt: Bestand darf nicht negativ werden"
        )
    if before + delta > Decimal("999999999999.999"):
        raise HTTPException(422, "Bestand überschreitet den darstellbaren Wertebereich")
    result = s.execute(
        update(StockArticle)
        .where(StockArticle.id == x.id, StockArticle.balance == before)
        .values(balance=before + delta)
    )
    if result.rowcount != 1:
        raise HTTPException(
            409, "Bestand wurde gleichzeitig verändert. Bitte erneut laden."
        )
    m = StockMovement(
        article_id=x.id,
        delta=delta,
        before=before,
        after=before + delta,
        reason=data.reason,
        note=data.note,
        event_id=data.event_id,
        actor_id=u.id,
        actor=u.username,
        external_id=data.external_id,
    )
    s.add(m)
    try:
        s.flush()
        audit(s, u, "stock.movement", m.id)
        s.commit()
    except IntegrityError:
        s.rollback()
        raise HTTPException(
            409, "Übertragungs-ID bereits verwendet; Buchung nicht wiederholen"
        )
    return movement_response(m)


@app.post("/api/stock/movements", status_code=201)
def movement(data: MovementInput, u=Depends(kitchen), s=Depends(db)):
    return book_movement(data, u, s)


@app.get("/api/stock/movements")
def movements(
    article_id: int | None = None, limit: int = 200, u=Depends(auth), s=Depends(db)
):
    stmt = (
        select(StockMovement)
        .order_by(StockMovement.id.desc())
        .limit(min(max(limit, 1), 2000))
    )
    if article_id:
        stmt = stmt.where(StockMovement.article_id == article_id)
    return [public(x) for x in s.scalars(stmt)]


@app.get("/api/integration/stock/articles")
def machine_articles(identity=Depends(machine), s=Depends(db)):
    scope(identity, "stock:read")
    return [
        article_public(x)
        for x in s.scalars(select(StockArticle).where(StockArticle.active == True))
    ]


@app.post("/api/integration/stock/movements", status_code=201)
def machine_movement(data: MovementInput, identity=Depends(machine), s=Depends(db)):
    u = scope(identity, "stock:write")
    if not data.external_id:
        raise HTTPException(
            422, "external_id ist für wiederholbare Übertragungen erforderlich"
        )
    return book_movement(data, u, s)


@app.get("/api/devices")
def devices(u=Depends(auth), s=Depends(db)):
    return [public(x) for x in s.scalars(select(Device).order_by(Device.name))]


@app.post("/api/devices", status_code=201)
def new_device(data: DeviceInput, u=Depends(kitchen), s=Depends(db)):
    x = Device(**data.model_dump())
    s.add(x)
    s.flush()
    audit(s, u, "device.create", x.id)
    s.commit()
    return public(x)


@app.put("/api/devices/{id}")
def change_device(id: int, data: DeviceInput, u=Depends(kitchen), s=Depends(db)):
    x = s.scalar(select(Device).where(Device.id == id).with_for_update())
    if not x:
        raise HTTPException(404, "Gerät nicht gefunden")
    for k, v in data.model_dump().items():
        setattr(x, k, v)
    audit(s, u, "device.limits.update", id)
    s.commit()
    return public(x)


def reading_public(s, r):
    d = public(r)
    d["outside"] = r.value < r.lower or r.value > r.upper
    d["notes"] = [
        public(n)
        for n in s.scalars(
            select(ReadingNote)
            .where(ReadingNote.reading_id == r.id)
            .order_by(ReadingNote.id)
        )
    ]
    return d


def save_reading(data, u, s, source):
    if utc(data.measured_at) > now() + timedelta(minutes=5):
        raise HTTPException(422, "Messzeitpunkt liegt in der Zukunft")
    if data.external_id:
        old = s.scalar(
            select(Reading).where(
                Reading.actor_id == u.id,
                Reading.device_id == data.device_id,
                Reading.external_id == data.external_id,
            )
        )
        if old:
            if (
                old.value != data.value
                or utc(old.measured_at) != utc(data.measured_at)
                or old.event_id != data.event_id
            ):
                raise HTTPException(
                    409, "Übertragungs-ID bereits mit anderen Daten verwendet"
                )
            return reading_public(s, old)
    if data.event_id is not None:
        event_access(s, data.event_id, u, True)
    device = s.scalar(
        select(Device).where(Device.id == data.device_id).with_for_update()
    )
    if not device or not device.active:
        raise HTTPException(404, "Aktives Gerät nicht gefunden")
    r = Reading(
        **data.model_dump(),
        lower=device.lower,
        upper=device.upper,
        device_name=device.name,
        device_location=device.location,
        actor_id=u.id,
        actor=u.username,
        source=source,
    )
    s.add(r)
    try:
        s.flush()
        audit(s, u, "temperature.record", device.id)
        s.commit()
    except IntegrityError:
        s.rollback()
        raise HTTPException(
            409, "Übertragungs-ID bereits verwendet; Anfrage erneut lesen"
        )
    return reading_public(s, r)


@app.post("/api/readings", status_code=201)
def new_reading(data: ReadingInput, u=Depends(auth), s=Depends(db)):
    return save_reading(data, u, s, "manual")


@app.post("/api/integration/readings", status_code=201)
def machine_reading(data: ReadingInput, identity=Depends(machine), s=Depends(db)):
    u = scope(identity, "temperature:write", data.device_id)
    if not data.external_id:
        raise HTTPException(422, "external_id ist erforderlich")
    return save_reading(data, u, s, "device")


@app.get("/api/readings")
def readings(
    device_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 2000,
    u=Depends(auth),
    s=Depends(db),
):
    get(s, Device, device_id)
    stmt = select(Reading).where(Reading.device_id == device_id)
    for value in (start, end):
        if value and value.tzinfo is None:
            raise HTTPException(422, "Zeitzone erforderlich")
    if start:
        stmt = stmt.where(Reading.measured_at >= start)
    if end:
        stmt = stmt.where(Reading.measured_at <= end)
    if u.role != "admin":
        stmt = stmt.where(
            (Reading.event_id.is_(None))
            | (
                Reading.event_id.in_(
                    select(Operation.id).where(Operation.deleted_at.is_(None))
                )
            )
        )
    rows = s.scalars(
        stmt.order_by(Reading.measured_at.desc(), Reading.id.desc()).limit(
            min(max(limit, 1), 10000)
        )
    ).all()
    return [reading_public(s, r) for r in reversed(rows)]


@app.post("/api/readings/{id}/notes", status_code=201)
def reading_note(id: int, data: NoteInput, u=Depends(kitchen), s=Depends(db)):
    r = get(s, Reading, id)
    if r.event_id and s.get(Operation, r.event_id):
        event_access(s, r.event_id, u, True)
    n = ReadingNote(reading_id=id, text=data.text, actor=u.username)
    s.add(n)
    audit(s, u, "temperature.note", id)
    s.commit()
    return public(n)


@app.get("/api/api-keys")
def keys(u=Depends(admin), s=Depends(db)):
    return [
        public(k, ("token_hash",))
        for k in s.scalars(select(ApiKey).order_by(ApiKey.id))
    ]


@app.post("/api/api-keys", status_code=201)
def create_key(data: KeyInput, u=Depends(admin), s=Depends(db)):
    owner = get(s, User, data.user_id)
    if owner.role != "technical" or not owner.active:
        raise HTTPException(422, "Aktiver technischer Benutzer erforderlich")
    for id in data.device_ids:
        get(s, Device, id)
    raw = secrets.token_urlsafe(48)
    k = ApiKey(**data.model_dump(), token_hash=stamp(raw))
    s.add(k)
    s.flush()
    audit(s, u, "apikey.create", k.id)
    s.commit()
    return {**public(k, ("token_hash",)), "token": raw}


@app.delete("/api/api-keys/{id}")
def revoke_key(id: int, u=Depends(admin), s=Depends(db)):
    k = get(s, ApiKey, id)
    k.active = False
    audit(s, u, "apikey.revoke", id)
    s.commit()
    return {"ok": True}


# Files are in PostgreSQL, backed up atomically with their ownership and retention metadata.
def document_access(s, d, u, write=False):
    if d.event_id:
        event_access(s, d.event_id, u, write)
    if write:
        role(u, KITCHEN)


@app.get("/api/documents")
def documents(
    event_id: int | None = None,
    template_id: int | None = None,
    u=Depends(auth),
    s=Depends(db),
):
    if event_id is not None:
        event_access(s, event_id, u)
    if template_id is not None:
        get(s, EventTemplate, template_id)
    stmt = (
        select(Document)
        .where(Document.event_id == event_id, Document.template_id == template_id)
        .order_by(Document.id.desc())
    )
    return [public(d, ("content",)) for d in s.scalars(stmt)]


async def read_pdf(file, u):
    cap = 50 * 1024 * 1024 if u.role == "admin" else 5 * 1024 * 1024
    raw = await file.read(cap + 1)
    if len(raw) > cap:
        raise HTTPException(413, "Datei zu groß (5 MB, Administrator maximal 50 MB)")
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
        if not raw.startswith(b"%PDF-") or doc.is_encrypted or not len(doc):
            raise ValueError()
    except (ValueError, RuntimeError):
        raise HTTPException(422, "Gültiges, unverschlüsseltes PDF erforderlich")
    return raw


@app.post("/api/documents", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    event_id: int | None = Form(None),
    template_id: int | None = Form(None),
    u=Depends(kitchen),
    s=Depends(db),
):
    if event_id is not None and template_id is not None:
        raise HTTPException(422, "Nur eine Zuordnung möglich")
    if event_id is not None:
        event_access(s, event_id, u, True)
    if template_id is not None:
        get(s, EventTemplate, template_id)
    raw = await read_pdf(file, u)
    d = Document(
        name=Path(file.filename or "Dokument.pdf").name[:200],
        content=raw,
        size=len(raw),
        event_id=event_id,
        template_id=template_id,
        actor=u.username,
    )
    s.add(d)
    s.flush()
    audit(s, u, "document.upload", d.id)
    s.commit()
    return public(d, ("content",))


@app.get("/api/documents/{id}/download")
def download_document(id: int, u=Depends(auth), s=Depends(db)):
    d = get(s, Document, id)
    document_access(s, d, u)
    return pdf_response(d.content, f"dokument-{id}.pdf")


@app.delete("/api/documents/{id}")
def remove_document(id: int, u=Depends(kitchen), s=Depends(db)):
    d = get(s, Document, id)
    document_access(s, d, u, True)
    s.delete(d)
    audit(s, u, "document.delete", id)
    s.commit()
    return {"ok": True}


def pdf_response(content, name):
    return Response(
        content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/form-templates")
def form_templates(u=Depends(kitchen), s=Depends(db)):
    return [
        public(t, ("content", "layout"))
        for t in s.scalars(
            select(FormTemplate).order_by(FormTemplate.code, FormTemplate.id)
        )
    ]


@app.get("/api/form-templates/{id}/layout")
def template_layout(id: int, u=Depends(kitchen), s=Depends(db)):
    return get(s, FormTemplate, id).layout


@app.post("/api/form-templates", status_code=201)
async def new_form_template(
    file: UploadFile = File(...),
    layout: str = Form(...),
    code: str = Form(...),
    name: str = Form(...),
    version: str = Form(...),
    u=Depends(admin),
    s=Depends(db),
):
    if not (
        code.startswith("Spez71-")
        and len(code) <= 20
        and 0 < len(name) <= 200
        and 0 < len(version) <= 100
    ):
        raise HTTPException(422, "Vorlagenbezeichnung ungültig")
    raw = await read_pdf(file, u)
    try:
        mapping = json.loads(layout)
    except ValueError:
        raise HTTPException(422, "Ungültiges Feldlayout")
    pdf_forms.validate_layout(mapping, raw)
    for old in s.scalars(select(FormTemplate).where(FormTemplate.code == code)):
        old.active = False
    t = FormTemplate(code=code, name=name, version=version, content=raw, layout=mapping)
    s.add(t)
    s.flush()
    audit(s, u, "form.template.version", t.id)
    s.commit()
    return public(t, ("content", "layout"))


def form_access(s, id, u, write=False):
    f = get(s, FormRecord, id)
    if f.event_id:
        event_access(s, f.event_id, u, write)
    if write:
        f = s.scalar(
            select(FormRecord)
            .where(FormRecord.id == id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if write and f.finalized_at:
        raise HTTPException(
            409,
            "Final gespeicherter Nachweis ist unveränderbar – auch für Administratoren",
        )
    return f


def form_public(f):
    return public(f, ("pdf",))


@app.get("/api/forms")
def forms(
    event_id: int | None = None,
    archived: bool = False,
    u=Depends(kitchen),
    s=Depends(db),
):
    if archived:
        role(u, {"admin"})
    if event_id is not None:
        event_access(s, event_id, u)
    stmt = (
        select(FormRecord)
        .where(FormRecord.event_id == event_id)
        .order_by(FormRecord.id.desc())
    )
    return [form_public(f) for f in s.scalars(stmt)]


@app.post("/api/forms", status_code=201)
def create_form(data: FormInput, u=Depends(kitchen), s=Depends(db)):
    t = get(s, FormTemplate, data.template_id)
    if not t.active:
        raise HTTPException(422, "Vorlage nicht aktiv")
    if data.event_id is not None:
        event_access(s, data.event_id, u, True)
    elif t.code not in ("Spez71-04", "Spez71-05", "Spez71-06", "Spez71-07"):
        raise HTTPException(422, "Diese Checkliste benötigt einen Vorgang")
    if data.correction_of:
        original = form_access(s, data.correction_of, u)
        if (
            not original.finalized_at
            or original.event_id != data.event_id
            or get(s, FormTemplate, original.template_id).code != t.code
        ):
            raise HTTPException(
                422, "Korrektur muss einen passenden finalen Nachweis referenzieren"
            )
    f = FormRecord(**data.model_dump(), created_by=u.username)
    s.add(f)
    s.flush()
    audit(s, u, "form.create", f.id)
    s.commit()
    return form_public(f)


@app.get("/api/forms/{id}")
def form_detail(id: int, u=Depends(kitchen), s=Depends(db)):
    f = form_access(s, id, u)
    t = get(s, FormTemplate, f.template_id)
    return {
        **form_public(f),
        "layout": t.layout,
        "template_name": t.name,
        "template_version": t.version,
    }


@app.put("/api/forms/{id}")
def save_form(id: int, data: FormPatch, u=Depends(kitchen), s=Depends(db)):
    f = form_access(s, id, u, True)
    t = get(s, FormTemplate, f.template_id)
    pdf_forms.render(t.content, t.layout, data.values, data.extra_pages)
    result = s.execute(
        update(FormRecord)
        .where(
            FormRecord.id == id,
            FormRecord.revision == data.revision,
            FormRecord.finalized_at.is_(None),
        )
        .values(
            values=data.values, extra_pages=data.extra_pages, revision=data.revision + 1
        )
    )
    if result.rowcount != 1:
        raise HTTPException(409, "Nachweis wurde gleichzeitig geändert. Neu laden.")
    audit(s, u, "form.save", id)
    s.commit()
    s.refresh(f)
    return form_public(f)


@app.post("/api/forms/{id}/finalize")
def finalize_form(id: int, data: FinalizeInput, u=Depends(lead), s=Depends(db)):
    f = form_access(s, id, u, True)
    t = get(s, FormTemplate, f.template_id)
    raw = pdf_forms.render(t.content, t.layout, f.values, f.extra_pages)
    if not any(f.values.values()):
        raise HTTPException(422, "Leerer Nachweis kann nicht finalisiert werden")
    result = s.execute(
        update(FormRecord)
        .where(
            FormRecord.id == id,
            FormRecord.revision == data.revision,
            FormRecord.finalized_at.is_(None),
        )
        .values(
            pdf=raw,
            pdf_hash=hashlib.sha256(raw).hexdigest(),
            finalized_at=now(),
            finalized_by=u.username,
            revision=data.revision + 1,
        )
    )
    if result.rowcount != 1:
        raise HTTPException(409, "Nachweis wurde gleichzeitig geändert. Neu laden.")
    audit(s, u, "form.finalize", id)
    s.commit()
    s.refresh(f)
    return form_public(f)


@app.get("/api/forms/{id}/pdf")
def form_pdf(id: int, u=Depends(kitchen), s=Depends(db)):
    f = form_access(s, id, u)
    t = get(s, FormTemplate, f.template_id)
    return pdf_response(
        f.pdf
        if f.finalized_at
        else pdf_forms.render(t.content, t.layout, f.values, f.extra_pages),
        f"{t.code}-{id}.pdf",
    )


@app.get("/api/forms/{id}/pages/{page_index}")
def form_page(id: int, page_index: int, u=Depends(kitchen), s=Depends(db)):
    f = form_access(s, id, u)
    t = get(s, FormTemplate, f.template_id)
    pages = pdf_forms.expanded_pages(t.layout, f.extra_pages)
    if not 0 <= page_index < len(pages):
        raise HTTPException(404, "Seite nicht gefunden")
    d = fitz.open(stream=f.pdf if f.finalized_at else t.content, filetype="pdf")
    p = d[page_index if f.finalized_at else pages[page_index][0]]
    return Response(
        p.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False).tobytes("png"),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/api/devices/{id}/export", status_code=201)
def export_temperature(id: int, data: ExportInput, u=Depends(lead), s=Depends(db)):
    device = get(s, Device, id)
    if data.end < data.start:
        raise HTTPException(422, "Endzeit liegt vor Beginn")
    if data.event_id is not None:
        event_access(s, data.event_id, u, True)
    stmt = (
        select(Reading)
        .where(
            Reading.device_id == id,
            Reading.measured_at >= data.start,
            Reading.measured_at <= data.end,
        )
        .order_by(Reading.measured_at, Reading.id)
    )
    # Do not resurrect private archived event data in a new ordinary record.
    stmt = stmt.where(
        (Reading.event_id.is_(None))
        | (
            Reading.event_id.in_(
                select(Operation.id).where(Operation.deleted_at.is_(None))
            )
        )
    )
    all_rows = s.scalars(stmt.limit(100001)).all()
    if len(all_rows) > 100000:
        raise HTTPException(422, "Zeitraum zu groß; bitte eingrenzen")
    selected = {}
    for r in all_rows:
        bucket = int(
            (utc(r.measured_at) - utc(data.start)).total_seconds()
            // (data.interval_hours * 3600)
        )
        selected.setdefault(bucket, r)
    rows = list(selected.values())
    if not rows:
        raise HTTPException(422, "Keine Messwerte im Zeitraum")
    code = "Spez71-06" if device.kind == "Tiefkühllager" else "Spez71-07"
    t = s.scalar(
        select(FormTemplate)
        .where(FormTemplate.code == code, FormTemplate.active == True)
        .order_by(FormTemplate.id.desc())
    )
    if not t:
        raise HTTPException(422, "Keine aktive Temperaturvorlage")
    fields = t.layout["pages"][0]["fields"]
    table_fields = [f for f in fields if "row" in f]
    capacity = max((f["row"] for f in table_fields), default=-1) + 1
    if capacity < 1:
        raise HTTPException(422, "Vorlage benötigt Tabellenfelder mit row und column")
    extra = (len(rows) - 1) // capacity
    if extra > 100:
        raise HTTPException(422, "Export hat mehr als 101 Seiten; Zeitraum verkürzen")
    values = {}
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Europe/Berlin")
    for index, r in enumerate(rows):
        pi, ri = divmod(index, capacity)
        local = utc(r.measured_at).astimezone(zone)
        notes = "; ".join(
            n.text
            for n in s.scalars(
                select(ReadingNote).where(ReadingNote.reading_id == r.id)
            )
        )
        # Snapshot label avoids rewriting historical device identity after renaming.
        for f in fields:
            if "row" not in f:
                values[pdf_forms.field_key(pi, f)] = (
                    f"{r.device_name} / {r.device_location}"
                )
            elif f["row"] == ri:
                value = {
                    0: local.strftime("%d.%m.%Y"),
                    1: local.strftime("%H:%M"),
                    2: str(r.value),
                    3: r.actor if r.source == "manual" else f"Gerät: {r.actor}",
                    5: notes if data.include_notes else "",
                }.get(f["column"], "")
                values[pdf_forms.field_key(pi, f)] = value
    # The export is an editable draft until separately finalized by OV-Stab/admin.
    pdf_forms.render(t.content, t.layout, values, extra)
    f = FormRecord(
        template_id=t.id,
        event_id=data.event_id,
        title=f"{device.name}: Temperaturkontrolle"[:200],
        values=values,
        extra_pages=extra,
        created_by=u.username,
    )
    s.add(f)
    s.flush()
    audit(s, u, "temperature.export", f.id)
    s.commit()
    omitted = sum(
        (r.value < r.lower or r.value > r.upper) and r.id not in {x.id for x in rows}
        for r in all_rows
    )
    return {
        **form_public(f),
        "selected_count": len(rows),
        "total_count": len(all_rows),
        "omitted_outliers": omitted,
        "selection": "Erster tatsächlicher Messwert pro Zeitfenster ab Startzeit",
    }


@app.get("/api/audit")
def audit_list(u=Depends(admin), s=Depends(db)):
    return [
        public(a) for a in s.scalars(select(Audit).order_by(Audit.id.desc()).limit(500))
    ]


@app.get("/api/admin/retention")
def retention(u=Depends(admin), s=Depends(db)):
    return [
        public(e)
        for e in s.scalars(
            select(Operation)
            .where(Operation.deleted_at.is_not(None))
            .order_by(Operation.retention_until)
        )
    ]


@app.get("/api/health")
def health(s=Depends(db)):
    s.execute(text("SELECT 1"))
    return {"status": "ok", "version": "2.0.0"}


FRONTEND = Path(__file__).parent.parent / "frontend"
app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
