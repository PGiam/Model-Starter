import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TEAM_PASSWORD", "team-pw")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pw")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    from app import store, main
    importlib.reload(store)
    importlib.reload(main)
    return TestClient(main.app)


def test_login_required(client):
    assert client.get("/api/sessions").status_code == 401
    assert client.post("/api/login", json={"name": "A", "password": "nope"}).status_code == 401


def test_team_login_cannot_use_admin(client):
    assert client.post("/api/login", json={"name": "Ana", "password": "team-pw"}).json()["admin"] is False
    assert client.get("/api/me").json()["name"] == "Ana"
    assert client.get("/api/sessions").json() == []
    assert client.get("/api/admin/playbook").status_code == 403


def test_admin_playbook_versions(client):
    client.post("/api/login", json={"name": "Phil", "password": "admin-pw"})
    pb = client.get("/api/admin/playbook").json()
    assert "Data Sources" in pb["text"]
    v = client.put("/api/admin/playbook", json={"text": pb["text"] + "\n- New rule.", "reason": "test"}).json()["version"]
    assert v == 1
    assert client.post("/api/admin/playbook/revert/0").json()["version"] == 2
    assert client.get("/api/admin/playbook").json()["text"] == pb["text"]


def test_file_download_is_confined(client):
    client.post("/api/login", json={"name": "Ana", "password": "team-pw"})
    from app import store
    sid = "a" * 16
    os.makedirs(os.path.join(store.session_dir(sid), "files"))
    with open(os.path.join(store.session_dir(sid), "files", "X_Model_v1.xlsx"), "wb") as f:
        f.write(b"x")
    assert client.get(f"/api/sessions/{sid}/files").json()[0]["model"] is True
    assert client.get(f"/api/sessions/{sid}/files/X_Model_v1.xlsx").status_code == 200
    assert client.get(f"/api/sessions/{sid}/files/..%2Fmeta.json").status_code == 404


def test_pages_served(client):
    assert "Model Starter" in client.get("/login.html").text
    assert client.get("/app.js").status_code == 200
