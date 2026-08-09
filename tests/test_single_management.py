import os
from pathlib import Path
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
    assert "-m" in a and "illico_ingest" in a and "https://x.io" in a
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
    assert "-m" in a and "illico_compile" in a and "--lint" in a
    assert "--lang" in a and "de" in a and "--tenant" not in a


def test_graph_rebuild_argv(client, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    captured = {}

    async def fake_run(job_id, argv):
        captured["argv"] = argv

    with patch("illico_single._run_job", new=fake_run):
        client.post("/api/graph/rebuild", json={})
    a = captured["argv"]
    assert "-m" in a and "illico_compile" in a and "--graph-only" in a and "--tenant" not in a


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


import zipfile
import io


def _bestand_anlegen(data):
    (data / "raw").mkdir(parents=True, exist_ok=True)
    (data / "raw" / "s1.md").write_text("Seite", encoding="utf-8")
    (data / "chats" / "single").mkdir(parents=True, exist_ok=True)
    (data / "chats" / "single" / "c1.json").write_text("{}", encoding="utf-8")


def test_export_liefert_ein_zip(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    _bestand_anlegen(tmp_path)

    r = client.get("/api/export")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "illico-export-" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert "illico-data/raw/s1.md" in z.namelist()


def test_export_ohne_chats(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    _bestand_anlegen(tmp_path)

    r = client.get("/api/export?chats=false")

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert not [n for n in z.namelist() if "/chats/" in n]


def test_export_raeumt_die_temp_datei_ab(client, tmp_path, monkeypatch):
    """Sonst fuellt jeder Download die Platte des Servers."""
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    _bestand_anlegen(tmp_path)
    gemerkt = []
    echtes_mkdtemp = illico_single.tempfile.mkdtemp

    def merkend(*a, **kw):
        pfad = echtes_mkdtemp(*a, **kw)
        gemerkt.append(Path(pfad))
        return pfad

    monkeypatch.setattr(illico_single.tempfile, "mkdtemp", merkend)

    r = client.get("/api/export")

    assert r.status_code == 200
    assert gemerkt, "die Route muss ein Temp-Verzeichnis angelegt haben"
    assert not gemerkt[0].exists(), "das Temp-Verzeichnis muss nach dem Senden weg sein"


def test_export_warnt_bei_laufendem_job(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    _bestand_anlegen(tmp_path)
    illico_single.jobs.clear()
    illico_single._new_job("compile-1", "compile")

    r = client.get("/api/export")

    illico_single.jobs.clear()
    assert "x-illico-warning" in r.headers
    assert "compile" in r.headers["x-illico-warning"]


def test_export_ohne_job_ohne_warnung(client, tmp_path, monkeypatch):
    """Eine Dauerwarnung wird ueberlesen."""
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    _bestand_anlegen(tmp_path)
    illico_single.jobs.clear()

    r = client.get("/api/export")

    assert "x-illico-warning" not in r.headers


def test_export_ohne_datenverzeichnis_404(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    monkeypatch.setattr("illico_app.DATA_DIR", tmp_path / "gibt-es-nicht")

    r = client.get("/api/export")

    assert r.status_code == 404


def test_export_verlangt_token_wenn_gesetzt(client, monkeypatch):
    monkeypatch.setenv("ILLICO_SINGLE_TOKEN", "geheim")
    r = client.get("/api/export")
    assert r.status_code == 401
