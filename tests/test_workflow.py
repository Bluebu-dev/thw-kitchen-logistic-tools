import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import fitz
import pytest
from sqlalchemy import select, text, update, func
from sqlalchemy.exc import DBAPIError
from backend import main as m
from backend.models import (
    Reading,
    StockArticle,
    StockMovement,
    User,
    SessionLocal,
    engine,
    now,
    FormRecord,
    Operation,
)
from backend import pdf_forms
from conftest import login_as


def post(c, path, data, status=201):
    r = c.post("/api" + path, json=data)
    assert r.status_code == status, r.text
    return r.json()


def template(c, name="Plan"):
    return post(
        c,
        "/event-templates",
        {
            "name": name,
            "categories": [
                {"name": "Material", "items": ["Wasser", "Töpfe"]},
                {"name": "Abschluss", "items": ["Reinigung"]},
            ],
        },
    )


def operation(c, ids=()):
    return post(
        c,
        "/events",
        {
            "name": "Testeinsatz",
            "date": "2026-11-03",
            "end_date": "2026-11-05",
            "template_ids": list(ids),
        },
    )


def article(c, unit="Liter"):
    return post(
        c,
        "/stock/articles",
        {"name": "Trinkwasser", "unit": unit, "target": "3.125", "kind": "Getränk"},
    )


def device(c):
    return post(
        c,
        "/devices",
        {"name": "Kühlschrank", "location": "Küche", "lower": "0", "upper": "7"},
    )


def form(c, code="Spez71-04", event_id=None):
    t = next(
        t
        for t in c.get("/api/form-templates").json()
        if t["code"] == code and t["active"]
    )
    return post(
        c,
        "/forms",
        {"template_id": t["id"], "event_id": event_id, "title": "Kontrolle"},
    )


def fill(c, f):
    d = c.get("/api/forms/" + str(f["id"])).json()
    field = next(x for x in d["layout"]["pages"][0]["fields"] if x["kind"] == "text")
    r = c.put(
        "/api/forms/" + str(f["id"]),
        json={
            "values": {"0:" + field["id"]: "Passau"},
            "extra_pages": 0,
            "revision": d["revision"],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_auth_roles_and_csrf(admin):
    c = admin
    r = c.post(
        "/api/auth/login",
        json={"username": "technical", "password": "test-password-long"},
    )
    assert r.status_code == 401
    t = template(c)
    e = operation(c, [t["id"]])
    a = article(c)
    d = device(c)
    login_as(c, "user")
    assert c.get("/api/stock/articles").status_code == 200
    assert (
        c.patch(
            f"/api/events/{e['id']}/items/{e['items'][0]['id']}", json={"done": True}
        ).status_code
        == 200
    )
    assert (
        c.post(
            "/api/events/" + str(e["id"]) + "/items",
            json={"label": "Test", "category": "Material"},
        ).status_code
        == 403
    )
    assert (
        c.post("/api/stock/articles", json={"name": "Hack", "unit": "kg"}).status_code
        == 403
    )
    assert c.get("/api/forms").status_code == 403
    post(
        c,
        "/readings",
        {"device_id": d["id"], "value": "4.125", "measured_at": now().isoformat()},
    )
    assert c.put("/api/settings", json={"ov_name": "Hack"}).status_code == 403
    del c.headers["X-CSRF-Token"]
    assert (
        c.post(
            "/api/readings",
            json={"device_id": d["id"], "value": "4", "measured_at": now().isoformat()},
        ).status_code
        == 403
    )
    login_as(c, "cook")
    post(c, f"/events/{e['id']}/items", {"label": "Extra", "category": "Zusatz"})
    f = fill(c, form(c))
    assert (
        c.post(
            f"/api/forms/{f['id']}/finalize", json={"revision": f["revision"]}
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/api/stock/movements",
            json={
                "article_id": a["id"],
                "direction": "inventory",
                "quantity": "0",
                "reason": "Inventurkorrektur",
                "note": "Zählung",
            },
        ).status_code
        == 403
    )


def test_combined_templates_snapshots_and_documents(admin):
    c = admin
    t1 = template(c, "A")
    t2 = template(c, "B")
    raw = Path("backend/forms/Spez71-06.pdf").read_bytes()
    assert (
        c.post(
            "/api/documents",
            files={"file": ("auftrag.pdf", raw, "application/pdf")},
            data={"template_id": t1["id"]},
        ).status_code
        == 201
    )
    e = operation(c, [t1["id"], t2["id"]])
    assert len(e["items"]) == 6 and e["categories"] == ["Material", "Abschluss"]
    c.put(
        "/api/event-templates/" + str(t1["id"]),
        json={"name": "Geändert", "categories": []},
    )
    assert len(c.get("/api/events/" + str(e["id"])).json()["items"]) == 6
    docs = c.get("/api/documents?event_id=" + str(e["id"])).json()
    assert len(docs) == 1
    assert c.get("/api/documents/" + str(docs[0]["id"]) + "/download").content == raw
    r = c.post(
        "/api/events",
        json={"name": "Invalid", "date": "2026-11-04", "end_date": "2026-11-03"},
    )
    assert r.status_code == 422


def test_decimal_ledger_inventory_and_retries(admin):
    c = admin
    a = article(c)
    payload = {
        "article_id": a["id"],
        "direction": "in",
        "quantity": "1.125",
        "reason": "Zugang",
        "note": "Lieferung",
        "external_id": "supply-1",
    }
    first = post(c, "/stock/movements", payload)
    assert first["after"] == "1.125"
    assert post(c, "/stock/movements", payload)["id"] == first["id"]
    assert (
        c.post("/api/stock/movements", json={**payload, "quantity": "2"}).status_code
        == 409
    )
    payload.pop("external_id")
    assert (
        c.post(
            "/api/stock/movements",
            json={
                **payload,
                "direction": "out",
                "quantity": "1.126",
                "reason": "Verbrauch",
            },
        ).status_code
        == 409
    )
    assert (
        c.post(
            "/api/stock/movements", json={**payload, "quantity": "0.0001"}
        ).status_code
        == 422
    )
    post(
        c,
        "/stock/movements",
        {
            **payload,
            "direction": "inventory",
            "quantity": "1.125",
            "reason": "Inventurkorrektur",
            "note": "Unverändert gezählt",
        },
    )
    inventory = post(
        c,
        "/stock/movements",
        {
            **payload,
            "direction": "inventory",
            "quantity": "5.001",
            "reason": "Inventurkorrektur",
            "note": "Ist-Bestand gezählt",
        },
    )
    assert inventory["delta"] == "3.876"
    assert (
        c.put(
            "/api/stock/articles/" + str(a["id"]),
            json={"name": "Wasser", "unit": "kg", "target": "3"},
        ).status_code
        == 409
    )
    with SessionLocal() as s:
        for table in ("stock_movements",):
            with pytest.raises(DBAPIError):
                s.execute(
                    text(f"UPDATE {table} SET note='manipulated' WHERE id=:id"),
                    {"id": first["id"]},
                )
                s.commit()
            s.rollback()


def test_temperature_immutable_limits_notes_and_export(admin):
    c = admin
    d = device(c)
    base = now() - timedelta(days=2)
    for n in range(18):
        post(
            c,
            "/readings",
            {
                "device_id": d["id"],
                "value": "9.125" if n % 2 else "4.125",
                "measured_at": (base + timedelta(hours=n)).isoformat(),
            },
        )
    rows = c.get("/api/readings", params={"device_id": d["id"]}).json()
    assert rows[1]["outside"]
    post(c, f"/readings/{rows[1]['id']}/notes", {"text": "Reinigung der Kühltruhe"})
    c.put(
        "/api/devices/" + str(d["id"]),
        json={
            "name": "Umbenannt",
            "location": "Neue Küche",
            "lower": "0",
            "upper": "12",
        },
    )
    rows2 = c.get("/api/readings", params={"device_id": d["id"]}).json()
    assert rows2[1]["upper"] == "7.000" and rows2[1]["outside"]
    assert rows2[1]["notes"][0]["text"] == "Reinigung der Kühltruhe"
    assert c.patch(
        "/api/readings/" + str(rows[0]["id"]), json={"value": 2}
    ).status_code in (404, 405)
    f = post(
        c,
        f"/devices/{d['id']}/export",
        {
            "start": base.isoformat(),
            "end": (base + timedelta(hours=20)).isoformat(),
            "interval_hours": 1,
        },
    )
    assert f["selected_count"] == 18 and f["extra_pages"] == 1
    raw = c.get("/api/forms/" + str(f["id"]) + "/pdf").content
    pdf = fitz.open(stream=raw, filetype="pdf")
    assert len(pdf) == 2
    assert "Seite 1 von 2" in pdf[0].get_text() and "Seite 2 von 2" in pdf[1].get_text()
    assert "4.125" in pdf[0].get_text() and "Kühlschrank" in pdf[0].get_text()
    sparse = post(
        c,
        f"/devices/{d['id']}/export",
        {
            "start": base.isoformat(),
            "end": (base + timedelta(hours=20)).isoformat(),
            "interval_hours": 3,
        },
    )
    assert sparse["selected_count"] == 6 and sparse["omitted_outliers"] > 0
    with SessionLocal() as s:
        with pytest.raises(DBAPIError):
            s.execute(
                update(Reading).where(Reading.id == rows[0]["id"]).values(value=1)
            )
            s.commit()
        s.rollback()


def test_finalization_archive_access_and_retention(admin):
    c = admin
    e = operation(c)
    f = fill(c, form(c, event_id=e["id"]))
    r = c.post(f"/api/forms/{f['id']}/finalize", json={"revision": f["revision"]})
    assert r.status_code == 200, r.text
    raw = c.get(f"/api/forms/{f['id']}/pdf").content
    assert hashlib.sha256(raw).hexdigest() == r.json()["pdf_hash"]
    expected_preview = (
        fitz.open(stream=raw, filetype="pdf")[0]
        .get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
        .tobytes("png")
    )
    assert c.get(f"/api/forms/{f['id']}/pages/0").content == expected_preview
    assert (
        c.put(
            f"/api/forms/{f['id']}",
            json={"values": {}, "extra_pages": 0, "revision": r.json()["revision"]},
        ).status_code
        == 409
    )
    assert (
        c.post(
            f"/api/forms/{f['id']}/finalize", json={"revision": r.json()["revision"]}
        ).status_code
        == 409
    )
    with SessionLocal() as s:
        with pytest.raises(DBAPIError):
            s.execute(
                update(FormRecord)
                .where(FormRecord.id == f["id"])
                .values(title="Changed")
            )
            s.commit()
        s.rollback()
    archived = c.delete("/api/events/" + str(e["id"]))
    assert archived.status_code == 200
    assert (
        datetime.fromisoformat(archived.json()["retention_until"]).year
        == now().year + 10
    )
    assert c.get(f"/api/forms/{f['id']}/pdf").content == raw
    assert (
        c.post(
            "/api/documents",
            files={"file": ("a.pdf", raw, "application/pdf")},
            data={"event_id": e["id"]},
        ).status_code
        == 409
    )
    login_as(c, "staff")
    assert c.get("/api/events/" + str(e["id"])).status_code == 404
    assert c.get("/api/events?archived=true").status_code == 403
    assert c.get(f"/api/forms/{f['id']}").status_code == 404
    assert c.get(f"/api/forms/{f['id']}/pdf").status_code == 404
    assert c.get(f"/api/forms/{f['id']}/pages/0").status_code == 404
    assert m.purge_expired() == 0
    with SessionLocal() as s:
        event = s.get(Operation, e["id"])
        event.deleted_at = now() - timedelta(days=4000)
        event.retention_until = now() - timedelta(days=1)
        s.commit()
    assert m.purge_expired() == 1
    with SessionLocal() as s:
        assert s.get(FormRecord, f["id"]) is None and s.get(Operation, e["id"]) is None


def test_concurrent_draft_revision_and_empty_finalize(admin):
    c = admin
    f = form(c)
    assert (
        c.post(
            f"/api/forms/{f['id']}/finalize", json={"revision": f["revision"]}
        ).status_code
        == 422
    )
    fill(c, f)
    assert (
        c.put(
            f"/api/forms/{f['id']}",
            json={"values": {}, "extra_pages": 0, "revision": 1},
        ).status_code
        == 409
    )


def test_machine_keys_scope_replay_and_revocation(admin):
    c = admin
    users = c.get("/api/users").json()
    owner = next(u for u in users if u["role"] == "technical")
    d = device(c)
    d2 = device(c)
    k = post(
        c,
        "/api-keys",
        {
            "user_id": owner["id"],
            "name": "Sensor",
            "scopes": ["temperature:write"],
            "device_ids": [d["id"]],
        },
    )
    headers = {"Authorization": "Bearer " + k["token"]}
    body = {
        "device_id": d["id"],
        "value": "-0.125",
        "measured_at": (now() - timedelta(hours=1)).isoformat(),
        "external_id": "sensor-message-1",
    }
    r = c.post("/api/integration/readings", json=body, headers=headers)
    assert r.status_code == 201, r.text
    repeated = c.post("/api/integration/readings", json=body, headers=headers)
    assert repeated.json()["id"] == r.json()["id"]
    assert (
        c.post(
            "/api/integration/readings", json={**body, "value": "1"}, headers=headers
        ).status_code
        == 409
    )
    assert (
        c.post(
            "/api/integration/readings",
            json={**body, "device_id": d2["id"]},
            headers=headers,
        ).status_code
        == 403
    )
    assert c.get("/api/integration/stock/articles", headers=headers).status_code == 403
    assert "token_hash" not in str(c.get("/api/api-keys").json())
    c.delete("/api/api-keys/" + str(k["id"]))
    assert (
        c.post("/api/integration/readings", json=body, headers=headers).status_code
        == 401
    )


def test_upload_validation_and_branding(admin):
    c = admin
    assert (
        c.post(
            "/api/documents",
            files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
        ).status_code
        == 422
    )
    assert (
        c.put("/api/settings", json={"ov_name": "Ortsverband Test"}).status_code == 200
    )
    assert c.get("/api/settings").json()["ov_name"] == "Ortsverband Test"
    logo = Path("frontend/thw-logo.png").read_bytes()
    assert (
        c.put(
            "/api/settings/logo", files={"file": ("logo.png", logo, "image/png")}
        ).status_code
        == 200
    )
    assert c.get("/api/settings/logo").headers["content-type"] == "image/png"
    assert (
        c.put(
            "/api/settings/logo",
            files={"file": ("bad.svg", b"<svg/>", "image/svg+xml")},
        ).status_code
        == 422
    )
    login_as(c, "cook")
    assert (
        c.post(
            "/api/documents",
            files={
                "file": (
                    "large.pdf",
                    b"%PDF-" + b"x" * (5 * 1024 * 1024),
                    "application/pdf",
                )
            },
        ).status_code
        == 413
    )


@pytest.mark.parametrize("code", ["02", "03", "04", "05", "06", "07", "08", "09"])
def test_all_originals_blank_pixel_identity_and_fill(code):
    p = Path(f"backend/forms/Spez71-{code}.pdf")
    raw = p.read_bytes()
    layout = json.loads(p.with_suffix(".json").read_text())
    original = fitz.open(stream=raw, filetype="pdf")
    blank = fitz.open(stream=pdf_forms.render(raw, layout, {}), filetype="pdf")
    assert len(original) == len(blank)
    for before, after in zip(original, blank):
        assert before.get_pixmap().samples == after.get_pixmap().samples
    values = {}
    for pi, page in enumerate(layout["pages"]):
        for field in page["fields"]:
            values[pdf_forms.field_key(pi, field)] = (
                True if field["kind"] == "checkbox" else "Test"
            )
    filled = fitz.open(stream=pdf_forms.render(raw, layout, values), filetype="pdf")
    assert len(filled) == len(original)
    assert "Test" in "".join(p.get_text() for p in filled)


def test_concurrent_postgres_withdrawals(admin):
    if engine.dialect.name != "postgresql":
        pytest.skip("Row-lock concurrency is verified against PostgreSQL")
    a = article(admin)
    post(
        admin,
        "/stock/movements",
        {
            "article_id": a["id"],
            "direction": "in",
            "quantity": "1.000",
            "reason": "Zugang",
            "note": "Initial",
        },
    )

    def withdraw():
        with SessionLocal() as s:
            u = s.scalar(select(User).where(User.username == "admin"))
            try:
                m.book_movement(
                    m.MovementInput(
                        article_id=a["id"],
                        direction="out",
                        quantity="1.000",
                        reason="Verbrauch",
                        note="Gleichzeitige Entnahme",
                    ),
                    u,
                    s,
                )
                return 201
            except m.HTTPException as e:
                return e.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: withdraw(), range(2)))
    assert sorted(outcomes) == [201, 409]
    with SessionLocal() as s:
        assert s.get(StockArticle, a["id"]).balance == 0
        assert (
            s.scalar(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.article_id == a["id"])
            )
            == 2
        )


def test_new_original_version_keeps_existing_final_pdf(admin):
    c = admin
    f = fill(c, form(c, "Spez71-05"))
    r = c.post(f"/api/forms/{f['id']}/finalize", json={"revision": f["revision"]})
    assert r.status_code == 200
    original = c.get(f"/api/forms/{f['id']}/pdf").content
    path = Path("backend/forms/Spez71-05.pdf")
    r = c.post(
        "/api/form-templates",
        files={"file": ("updated.pdf", path.read_bytes(), "application/pdf")},
        data={
            "code": "Spez71-05",
            "name": "Lagerhygiene neue Fassung",
            "version": "Testfassung",
            "layout": path.with_suffix(".json").read_text(),
        },
    )
    assert r.status_code == 201, r.text
    assert c.get(f"/api/forms/{f['id']}/pdf").content == original
    templates = c.get("/api/form-templates").json()
    assert not next(t for t in templates if t["id"] == f["template_id"])["active"]
    assert next(t for t in templates if t["id"] == r.json()["id"])["active"]
