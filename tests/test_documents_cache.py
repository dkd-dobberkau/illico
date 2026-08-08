"""Extraktions-Cache: was einmal extrahiert wurde, wird nicht neu geschrieben.

Adressiert ueber die PDF-Bytes, nicht ueber das erzeugte Markdown — ein
Vision-LLM liefert bei jedem Lauf leicht anderes Markdown, und content_hash()
im Destillat-Cache haengt am Rumpf der raw/-Datei.
"""
import json
from pathlib import Path

import illico_documents as docs


def test_hash_haengt_am_inhalt_nicht_am_namen(tmp_path: Path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"gleicher inhalt")
    b.write_bytes(b"gleicher inhalt")
    assert docs.file_hash(a) == docs.file_hash(b)
    assert docs.file_hash(a).startswith("sha256:")

    b.write_bytes(b"anderer inhalt")
    assert docs.file_hash(a) != docs.file_hash(b)


def test_fehlendes_manifest_ist_leer(tmp_path: Path):
    assert docs.load_manifest(tmp_path / "gibt-es-nicht.json") == {}


def test_kaputtes_manifest_ist_leer_statt_toedlich(tmp_path: Path):
    path = tmp_path / "_documents.json"
    path.write_text("{kein json", encoding="utf-8")
    assert docs.load_manifest(path) == {}


def test_manifest_ueberlebt_den_roundtrip(tmp_path: Path):
    path = tmp_path / "_documents.json"
    manifest = {"sha256:abc": {"source": "a.pdf", "label": "l",
                               "pages_total": 3, "pages_done": [1, 2]}}
    docs.save_manifest(path, manifest)
    assert docs.load_manifest(path) == manifest
    assert json.loads(path.read_text(encoding="utf-8")) == manifest


def test_unbekanntes_dokument_braucht_alle_seiten():
    assert docs.pending_pages(None, 3) == [1, 2, 3]


def test_vollstaendiges_dokument_braucht_nichts():
    entry = {"pages_total": 3, "pages_done": [1, 2, 3]}
    assert docs.pending_pages(entry, 3) == []


def test_teilausfall_wird_gezielt_nachgeholt():
    entry = {"pages_total": 3, "pages_done": [1, 3]}
    assert docs.pending_pages(entry, 3) == [2]


def test_geaenderte_seitenzahl_erzwingt_vollen_neulauf():
    """Der Manifest-Schluessel ist der Datei-Hash, also kann sich pages_total
    eigentlich nicht aendern. Passiert es doch, ist der Eintrag unbrauchbar."""
    entry = {"pages_total": 3, "pages_done": [1, 2, 3]}
    assert docs.pending_pages(entry, 5) == [1, 2, 3, 4, 5]
