import importlib
from pathlib import Path
from fastapi.testclient import TestClient


def _make_client(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ILLICO_DATA", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    (tmp_path / "raw").mkdir(parents=True)
    wiki = tmp_path / "wiki"; wiki.mkdir()
    (wiki / "_index.md").write_text("# Index\n", encoding="utf-8")
    (wiki / "Thema.md").write_text('---\nsources: ["a.md"]\n---\n# Thema\n', encoding="utf-8")
    import illico_app
    importlib.reload(illico_app)
    return TestClient(illico_app.app)


def test_single_user_no_login_required(monkeypatch, tmp_path):
    c = _make_client(monkeypatch, tmp_path)
    # Ohne Cookie: Kern-App antwortet 200 (kein 401 wie in Cloud).
    r = c.get("/api/articles")
    assert r.status_code == 200
    # Artikel-Keys behalten die .md-Endung (Verhalten wie zuvor).
    assert "Thema.md" in r.json()


def test_single_user_has_no_admin_routes(monkeypatch, tmp_path):
    c = _make_client(monkeypatch, tmp_path)
    assert c.get("/api/admin/overview").status_code == 404
    assert c.post("/api/login", json={"code": "ABCDEF"}).status_code == 404


def test_health_ok(monkeypatch, tmp_path):
    c = _make_client(monkeypatch, tmp_path)
    assert c.get("/api/health").json() == {"ok": True}
