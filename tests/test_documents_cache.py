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
    """Der Manifest-Schluessel ist der Pfad, der Hash im Eintrag nur der
    Aenderungsdetektor — pages_total sollte sich bei gleichem Hash eigentlich
    nicht aendern. Passiert es doch (kaputtes Manifest), ist der Eintrag
    unbrauchbar und alles wird neu geholt."""
    entry = {"pages_total": 3, "pages_done": [1, 2, 3]}
    assert docs.pending_pages(entry, 5) == [1, 2, 3, 4, 5]


import pypdfium2 as pdfium
import pytest

from test_documents_pdf import MINIMAL_PDF
from test_documents_routing import FakeLLM


@pytest.fixture
def bestand(tmp_path: Path) -> Path:
    """Ordner mit zwei PDFs und einer Nicht-PDF-Datei.

    Die beiden PDFs sind ABSICHTLICH byte-gleich. Sie liegen an
    verschiedenen Pfaden und sind damit zwei Dokumente, die je eigene
    raw/-Dateien bekommen muessen. Ein frueherer Entwurf schluesselte das
    Manifest ueber den Datei-Hash — damit teilten sich beide einen Eintrag
    und das zweite verschwand still. Diese Gleichheit nicht "reparieren":
    sie ist der Regressionstest dafuer.
    """
    src = tmp_path / "bestand"
    (src / "unterordner").mkdir(parents=True)
    (src / "eins.pdf").write_bytes(MINIMAL_PDF)
    (src / "unterordner" / "zwei.pdf").write_bytes(MINIMAL_PDF)
    (src / "liesmich.txt").write_text("kein pdf", encoding="utf-8")
    return src


def test_findet_pdfs_rekursiv_und_zaehlt_den_rest(bestand: Path):
    root, pdfs, skipped = docs.find_pdfs(bestand)
    assert root == bestand
    assert len(pdfs) == 2
    assert skipped == 1


def test_einzelne_datei_ist_auch_zulaessig(bestand: Path):
    root, pdfs, skipped = docs.find_pdfs(bestand / "eins.pdf")
    assert pdfs == [bestand / "eins.pdf"]
    assert root == bestand
    assert skipped == 0


def test_schreibt_je_seite_eine_datei_unter_dem_label(tmp_path, bestand):
    """Der Textpfad end-to-end: PDF mit Textebene wird zur raw/-Datei, ohne
    dass ein Modell angefasst wird.

    `threshold=10` ist hier notwendig und der eigentliche Punkt: MINIMAL_PDFs
    Textebene hat nur 26 Zeichen und ginge unter dem Default von 200 als Scan
    durch. Mit der niedrigen Schwelle prueft der Test wirklich den kostenlosen
    Pfad statt nur die Antwort des gefaelschten Modells zurueckzulesen.
    """
    data = tmp_path / "illico-data"
    llm = FakeLLM()
    report = docs.ingest_documents(
        target=bestand, data=data, label="handbuecher",
        model="m", jobs=1, threshold=10, call=llm,
    )
    written = sorted((data / "raw" / "handbuecher").glob("*.md"))
    assert len(written) == 2
    assert report.documents == 2
    assert report.pages_text == 2 and report.pages_vision == 0
    assert llm.calls == 0, "eine Seite mit Textebene darf kein Modell kosten"
    body = written[0].read_text(encoding="utf-8")
    assert 'domain: "handbuecher"' in body
    assert "Hallo Illico" in body


def test_zweiter_lauf_ist_gratis(tmp_path, bestand):
    data = tmp_path / "illico-data"
    llm = FakeLLM()
    docs.ingest_documents(target=bestand, data=data, label="l",
                          model="m", jobs=1, call=llm)
    first = {p: p.read_bytes() for p in (data / "raw" / "l").glob("*.md")}

    llm.calls = 0
    report = docs.ingest_documents(target=bestand, data=data, label="l",
                                   model="m", jobs=1, call=llm)

    assert llm.calls == 0
    assert report.documents_skipped == 2
    assert {p: p.read_bytes() for p in (data / "raw" / "l").glob("*.md")} == first


def test_fresh_umgeht_den_cache(tmp_path, bestand):
    data = tmp_path / "illico-data"
    docs.ingest_documents(target=bestand, data=data, label="l",
                          model="m", jobs=1, call=FakeLLM())
    report = docs.ingest_documents(target=bestand, data=data, label="l",
                                   model="m", jobs=1, fresh=True,
                                   call=FakeLLM())
    assert report.documents_skipped == 0


def test_geaenderte_datei_wird_neu_extrahiert(tmp_path, bestand):
    """Der Hash im Eintrag ist der Aenderungsdetektor."""
    data = tmp_path / "illico-data"
    docs.ingest_documents(target=bestand, data=data, label="l",
                          model="m", jobs=1, call=FakeLLM())

    (bestand / "eins.pdf").write_bytes(MINIMAL_PDF + b"\n% geaendert\n")
    report = docs.ingest_documents(target=bestand, data=data, label="l",
                                   model="m", jobs=1, call=FakeLLM())

    assert report.documents == 1, "nur die geaenderte Datei darf neu laufen"
    assert report.documents_skipped == 1


def test_defektes_dokument_stoppt_den_lauf_nicht(tmp_path, bestand):
    (bestand / "kaputt.pdf").write_bytes(b"kein PDF")
    data = tmp_path / "illico-data"

    report = docs.ingest_documents(target=bestand, data=data, label="l",
                                   model="m", jobs=1, call=FakeLLM())

    assert report.documents == 2
    assert len(report.errors) == 1
    assert "kaputt.pdf" in report.errors[0]


def test_auth_fehler_bricht_sofort_ab(tmp_path, bestand, monkeypatch):
    import illico_llm

    monkeypatch.setattr(docs, "extract_text", lambda page: "")

    def boom(model, messages, system=None, max_tokens=2000, retries=3):
        raise illico_llm.LLMAuthError("kein Key")

    with pytest.raises(illico_llm.LLMAuthError):
        docs.ingest_documents(target=bestand, data=tmp_path / "d", label="l",
                              model="m", jobs=1, call=boom)


def test_gescheiterte_seite_wird_gemeldet_und_nachgeholt(tmp_path, bestand, monkeypatch):
    monkeypatch.setattr(docs, "extract_text", lambda page: "")
    data = tmp_path / "illico-data"

    class FlakyLLM(FakeLLM):
        """Scheitert genau einmal.

        Das Scheitern haengt an einem eigenen Flag, nicht am Aufrufzaehler:
        der Test setzt `calls` zwischen den Laeufen zurueck, um die Aufrufe des
        zweiten Laufs zu messen. Haengte der Fehler am Zaehler, scheiterte
        genau der gemessene Wiederholungsversuch wieder — die Assertion waere
        nicht erfuellbar.
        """

        def __init__(self):
            super().__init__()
            self.failed_once = False

        def __call__(self, model, messages, system=None, max_tokens=2000, retries=3):
            self.calls += 1
            if not self.failed_once:
                self.failed_once = True
                raise RuntimeError("Modell kaputt")
            return "# Seite\n"

    llm = FlakyLLM()
    first = docs.ingest_documents(target=bestand, data=data, label="l",
                                  model="m", jobs=1, call=llm)
    assert first.pages_failed == 1

    llm.calls = 0
    second = docs.ingest_documents(target=bestand, data=data, label="l",
                                   model="m", jobs=1, call=llm)
    assert llm.calls == 1, "nur die eine gescheiterte Seite darf nachgeholt werden"
    assert second.pages_failed == 0


def test_max_pages_begrenzt_den_ganzen_lauf(tmp_path, bestand):
    data = tmp_path / "illico-data"
    report = docs.ingest_documents(target=bestand, data=data, label="l",
                                   model="m", jobs=1, max_pages=1,
                                   call=FakeLLM())
    assert report.pages_text + report.pages_vision == 1
