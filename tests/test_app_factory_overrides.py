import importlib
from pathlib import Path
from fastapi.testclient import TestClient

# Bewusst `import illico_app` statt `from illico_app import create_app`: andere
# Tests im Core-Suite (z. B. tests/core/conftest.py, test_app_single_user.py)
# reloaden illico_app per `importlib.reload`. Ein modulweit gebundenes
# `create_app` würde nach so einem Reload gegen das VERALTETE Funktionsobjekt
# laufen, dessen Default-Argument `_DEFAULT_MGMT` nicht mehr mit dem aktuellen
# Modul-Sentinel identisch ist (klassische Reload-vs-Default-Value-Falle).
import illico_app


def _create_app(**kwargs):
    importlib.reload(illico_app)
    return illico_app.create_app(**kwargs)


def _paths(app) -> set[str]:
    """Registrierte Pfade über das OpenAPI-Schema — stabil über FastAPI-Versionen
    hinweg (neuere Versionen legen inkludierte Router als Wrapper-Objekte ohne
    `.path` in `app.routes` ab, sodass ein direktes Iterieren bricht bzw. falsch-
    grün wird)."""
    return set(app.openapi()["paths"].keys())


def test_frontend_path_override(tmp_path):
    custom = tmp_path / "custom.html"
    custom.write_text("<h1>MEIN CLOUD FRONTEND</h1>", encoding="utf-8")
    app = _create_app(frontend_path=custom)
    r = TestClient(app).get("/")
    assert "MEIN CLOUD FRONTEND" in r.text


def test_default_serves_core_index():
    app = _create_app()
    r = TestClient(app).get("/")
    assert r.status_code == 200


def test_management_router_default_registers_single():
    app = _create_app()
    paths = _paths(app)
    assert "/api/ingest" in paths and "/api/compile" in paths


def test_management_router_none_suppresses_single():
    app = _create_app(management_router=None)
    assert "/api/ingest" not in _paths(app)
