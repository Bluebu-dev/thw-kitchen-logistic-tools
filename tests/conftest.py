import os
import tempfile
from pathlib import Path

# Independent DB by default. CI can supply a dedicated PostgreSQL database.
if not os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = "sqlite:///" + str(
        Path(tempfile.mkdtemp()) / "test.db"
    )
else:
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-admin-password-only"
os.environ["COOKIE_SECURE"] = "false"
import pytest
from fastapi.testclient import TestClient
from backend.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client):
    r = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-password-only"},
    )
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf"]
    existing = {u["username"] for u in client.get("/api/users").json()}
    for name in ("staff", "cook", "user", "technical"):
        if name not in existing:
            result = client.post(
                "/api/users",
                json={"username": name, "role": name, "password": "test-password-long"},
            )
            assert result.status_code == 201, result.text
    return client


def login_as(client, username):
    r = client.post(
        "/api/auth/login", json={"username": username, "password": "test-password-long"}
    )
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf"]
    return client
