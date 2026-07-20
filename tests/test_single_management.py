import os
from unittest.mock import patch, AsyncMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import illico_single


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr("illico_app.DATA_DIR", tmp_path)
    app = FastAPI()
    app.include_router(illico_single.single_management_router)
    return TestClient(app)


def test_token_leer_offen(client, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    with patch("illico_single._run_job", new=AsyncMock()):
        r = client.post("/api/ingest", json={"url": "https://example.com", "depth": 1})
    assert r.status_code == 200
    assert r.json()["status"] == "started"


def test_token_gesetzt_ohne_header_401(client, monkeypatch):
    monkeypatch.setenv("ILLICO_SINGLE_TOKEN", "geheim")
    r = client.post("/api/ingest", json={"url": "https://example.com", "depth": 1})
    assert r.status_code == 401


def test_token_gesetzt_mit_header_200(client, monkeypatch):
    monkeypatch.setenv("ILLICO_SINGLE_TOKEN", "geheim")
    with patch("illico_single._run_job", new=AsyncMock()):
        r = client.post("/api/ingest", json={"url": "https://example.com", "depth": 1},
                        headers={"Authorization": "Bearer geheim"})
    assert r.status_code == 200


def test_ingest_argv_ohne_tenant(client, monkeypatch, tmp_path):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    captured = {}

    async def fake_run(job_id, argv):
        captured["argv"] = argv

    with patch("illico_single._run_job", new=fake_run):
        client.post("/api/ingest", json={"url": "https://x.io", "depth": 3})
    a = captured["argv"]
    assert "illico_ingest.py" in a and "https://x.io" in a
    assert "--depth" in a and "3" in a
    assert "--tenant" not in a and "--only-domains" not in a


def test_compile_argv_ohne_tenant(client, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    captured = {}

    async def fake_run(job_id, argv):
        captured["argv"] = argv

    with patch("illico_single._run_job", new=fake_run):
        client.post("/api/compile", json={"lint_only": True, "lang": "de"})
    a = captured["argv"]
    assert "illico_compile.py" in a and "--lint" in a
    assert "--lang" in a and "de" in a and "--tenant" not in a


def test_graph_rebuild_argv(client, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    captured = {}

    async def fake_run(job_id, argv):
        captured["argv"] = argv

    with patch("illico_single._run_job", new=fake_run):
        client.post("/api/graph/rebuild", json={})
    a = captured["argv"]
    assert "illico_compile.py" in a and "--graph-only" in a and "--tenant" not in a


def test_delete_raw_global(client, monkeypatch, tmp_path):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "example.com").mkdir()
    f = raw / "example.com" / "seite.md"
    f.write_text("---\nsource_url: https://example.com/seite\n---\ninhalt", encoding="utf-8")
    with patch("illico_app._raw_domain_map", return_value={"example.com/seite.md": "example.com"}):
        r = client.delete("/api/raw/example.com")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1
    assert not f.exists()


def test_jobs_polling(client, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    illico_single.jobs.clear()
    illico_single.jobs["job-1"] = {"type": "ingest", "status": "done", "output": "log", "started": "t", "finished": "t"}
    r = client.get("/api/jobs")
    assert r.status_code == 200 and "job-1" in r.json()
    assert "output" not in r.json()["job-1"]      # Liste ohne output
    r2 = client.get("/api/jobs/job-1")
    assert r2.json()["output"] == "log"           # Detail mit output
    assert client.get("/api/jobs/fehlt").status_code == 404
