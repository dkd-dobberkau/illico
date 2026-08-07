from pathlib import Path

from illico_distill import DistillStore

_D = {
    "schema": 1,
    "hash": "sha256:abc123",
    "sources": ["ordner/seite.md"],
    "domain": "example.com",
    "title": "Seite",
    "summary": "Kurzfassung.",
    "keypoints": ["Punkt"],
    "entities": [{"name": "Beispiel AG", "label": "Organisation", "props": {}}],
    "edges": [],
    "model": "test-model",
    "created": "2026-08-07T12:00:00",
}


def test_put_then_get_roundtrip(tmp_path: Path):
    store = DistillStore(tmp_path / "distill")
    store.put(_D)
    assert store.get("sha256:abc123") == _D


def test_has_reports_presence(tmp_path: Path):
    store = DistillStore(tmp_path / "distill")
    assert not store.has("sha256:abc123")
    store.put(_D)
    assert store.has("sha256:abc123")


def test_get_missing_returns_none(tmp_path: Path):
    store = DistillStore(tmp_path / "distill")
    assert store.get("sha256:fehlt") is None


def test_schema_version_is_in_path(tmp_path: Path):
    """Eine Prompt-/Schema-Aenderung soll den alten Cache nicht mitbenutzen."""
    store = DistillStore(tmp_path / "distill")
    store.put(_D)
    assert (tmp_path / "distill" / "v1" / "abc123.json").exists()

    store_v2 = DistillStore(tmp_path / "distill", schema=2)
    assert store_v2.get("sha256:abc123") is None


def test_put_is_atomic_leaves_no_tmp(tmp_path: Path):
    store = DistillStore(tmp_path / "distill")
    store.put(_D)
    leftovers = list((tmp_path / "distill" / "v1").glob("*.tmp"))
    assert leftovers == []


def test_corrupt_file_reads_as_missing(tmp_path: Path):
    """Ein abgebrochener Schreibvorgang darf den Lauf nicht killen — die Seite
    wird dann einfach neu destilliert."""
    store = DistillStore(tmp_path / "distill")
    store.put(_D)
    (tmp_path / "distill" / "v1" / "abc123.json").write_text("{kaputt", encoding="utf-8")
    assert store.get("sha256:abc123") is None
