"""Fixtures für die Illico-Test-Suite (Single-User-Kern)."""
import importlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Isoliertes Temp-Daten-Verzeichnis."""
    d = tmp_path / "illico-data"
    d.mkdir()
    (d / "raw").mkdir()
    (d / "wiki").mkdir()
    return d


@pytest.fixture
def single_client(monkeypatch, data_dir: Path):
    """Single-User-App (kein Login, festes wiki/)."""
    monkeypatch.setenv("ILLICO_DATA", str(data_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    import illico_app
    importlib.reload(illico_app)
    with TestClient(illico_app.app) as c:
        yield c
