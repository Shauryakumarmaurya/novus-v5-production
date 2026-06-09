"""Security smoke tests: API-key auth and path-traversal protection."""
import os

import pytest

import app as app_mod


@pytest.fixture()
def client():
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


KEY = os.environ["NOVUS_API_KEY"]


def test_protected_endpoints_require_key(client):
    # No key
    assert client.post("/list_local_pdfs", json={"folder_path": "/tmp"}).status_code == 401
    assert client.post("/export_pdf", json={"content_html": "<p>x</p>"}).status_code == 401
    assert client.get("/api/v1/screener_data?ticker=TCS").status_code == 401
    # Wrong key
    r = client.post(
        "/list_local_pdfs",
        json={"folder_path": "/tmp"},
        headers={"X-API-Key": "wrong-key"},
    )
    assert r.status_code == 401


def test_path_traversal_blocked(client):
    headers = {"X-API-Key": KEY}
    # Absolute path outside the allowlist
    r = client.post("/list_local_pdfs", json={"folder_path": "/etc"}, headers=headers)
    assert r.status_code == 403
    # `..` escape from an allowed root must not pass
    sneaky = os.path.expanduser("~/Desktop/../../../etc")
    r = client.post("/list_local_pdfs", json={"folder_path": sneaky}, headers=headers)
    assert r.status_code == 403
    r = client.post(
        "/ingest_local",
        json={"ticker": "TCS", "folder_path": sneaky},
        headers=headers,
    )
    assert r.status_code == 403


def test_resolve_allowed_folder():
    assert app_mod.resolve_allowed_folder("/etc") is None
    assert app_mod.resolve_allowed_folder(os.path.expanduser("~/Desktop/../..")) is None
    desktop = os.path.expanduser("~/Desktop")
    assert app_mod.resolve_allowed_folder(desktop) == os.path.realpath(desktop)
    assert app_mod.resolve_allowed_folder(None) is None


def test_health_open(client):
    # Health stays unauthenticated for load balancers (may 500 without Redis)
    assert client.get("/health").status_code in (200, 500)
