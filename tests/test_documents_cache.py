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


def test_save_manifest_ist_atomar(tmp_path: Path, monkeypatch):
    """Finding 2: save_manifest muss ueber temp-Datei + os.replace schreiben,
    wie illico_inventory.save_inventory, illico_distill.DistillStore.put und
    illico_crawl_status.save_crawl_status. Ohne den atomaren Swap wuerde ein
    Absturz mitten im Schreiben die Datei zerreissen; load_manifest schluckt
    den JSONDecodeError und liefert dann {} — der Fortschritt ALLER Labels
    waere weg, nicht nur der gerade geschriebene."""
    path = tmp_path / "_documents.json"
    docs.save_manifest(path, {"a": 1})
    original = path.read_text(encoding="utf-8")

    real_write_text = Path.write_text

    def boom(self, *args, **kwargs):
        if self.name.endswith(".tmp"):
            real_write_text(self, *args, **kwargs)
            raise OSError("Absturz mitten im Schreiben")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", boom)

    with pytest.raises(OSError):
        docs.save_manifest(path, {"a": 2})

    assert path.read_text(encoding="utf-8") == original, (
        "ein Absturz beim Schreiben der temp-Datei darf die bestehende "
        "Manifest-Datei nicht antasten"
    )


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
    """documents_skipped == 0 allein beweist wenig — ein leeres Manifest
    liesse das auch trivial zu. Der Aufrufzaehler zeigt, dass wirklich neu
    extrahiert wurde, nicht nur dass nichts uebersprungen wurde."""
    data = tmp_path / "illico-data"
    docs.ingest_documents(target=bestand, data=data, label="l",
                          model="m", jobs=1, call=FakeLLM())

    llm = FakeLLM()
    report = docs.ingest_documents(target=bestand, data=data, label="l",
                                   model="m", jobs=1, fresh=True,
                                   call=llm)

    assert report.documents_skipped == 0
    assert report.documents == 2
    assert llm.calls == 2, "fresh muss wirklich neu extrahieren, nicht nur nichts uebersprungen haben"


def test_zwei_labels_mit_gleichem_relativpfad_kollidieren_nicht(tmp_path):
    """_documents.json ist eine Datei fuer alle Labels. Ohne das Label im
    Manifest-Schluessel teilten sich zwei Ingests mit gleicher relativer
    Struktur einen Eintrag, und das zweite Dokument bekam keine raw/-Dateien
    — stiller Verlust, keine Fehlermeldung."""
    quelle_a = tmp_path / "quelle-a"
    quelle_a.mkdir()
    (quelle_a / "a.pdf").write_bytes(MINIMAL_PDF)

    quelle_b = tmp_path / "quelle-b"
    quelle_b.mkdir()
    (quelle_b / "a.pdf").write_bytes(MINIMAL_PDF)

    data = tmp_path / "illico-data"

    report_hb = docs.ingest_documents(target=quelle_a, data=data, label="handbuecher",
                                      model="m", jobs=1, call=FakeLLM())
    report_vt = docs.ingest_documents(target=quelle_b, data=data, label="vertraege",
                                      model="m", jobs=1, call=FakeLLM())

    assert report_hb.documents == 1 and report_hb.documents_skipped == 0
    assert report_vt.documents == 1 and report_vt.documents_skipped == 0
    assert len(list((data / "raw" / "handbuecher").glob("*.md"))) == 1
    assert len(list((data / "raw" / "vertraege").glob("*.md"))) == 1

    # Zweiter Lauf je Label: beide sind jetzt gecacht, keiner verschwindet
    # in den Eintraegen des jeweils anderen.
    report_hb2 = docs.ingest_documents(target=quelle_a, data=data, label="handbuecher",
                                       model="m", jobs=1, call=FakeLLM())
    report_vt2 = docs.ingest_documents(target=quelle_b, data=data, label="vertraege",
                                       model="m", jobs=1, call=FakeLLM())
    assert report_hb2.documents_skipped == 1
    assert report_vt2.documents_skipped == 1


def test_fresh_beruehrt_nur_das_eigene_label(tmp_path):
    """--fresh auf einem Label darf ein fremdes Label nicht in eine volle
    Vision-Neuextraktion zwingen. Vorher leerte `manifest = {}` die gesamte
    _documents.json statt nur die Eintraege des laufenden Labels."""
    quelle_hb = tmp_path / "hb"
    quelle_hb.mkdir()
    (quelle_hb / "hb.pdf").write_bytes(MINIMAL_PDF)

    quelle_vt = tmp_path / "vt"
    quelle_vt.mkdir()
    (quelle_vt / "vt.pdf").write_bytes(MINIMAL_PDF)

    data = tmp_path / "illico-data"

    docs.ingest_documents(target=quelle_hb, data=data, label="handbuecher",
                          model="m", jobs=1, call=FakeLLM())
    docs.ingest_documents(target=quelle_vt, data=data, label="vertraege",
                          model="m", jobs=1, call=FakeLLM())

    docs.ingest_documents(target=quelle_vt, data=data, label="vertraege",
                          model="m", jobs=1, fresh=True, call=FakeLLM())

    llm = FakeLLM()
    report = docs.ingest_documents(target=quelle_hb, data=data, label="handbuecher",
                                   model="m", jobs=1, call=llm)

    assert llm.calls == 0, "unberuehrtes Label darf nach fremdem --fresh keine Neuextraktion zahlen"
    assert report.documents_skipped == 1


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


def test_ingest_documents_weist_ausbrechendes_label_ab(tmp_path, bestand):
    data = tmp_path / "illico-data"
    with pytest.raises(ValueError):
        docs.ingest_documents(target=bestand, data=data, label="../../ausserhalb",
                              model="m", jobs=1, call=FakeLLM())
    assert not (tmp_path / "ausserhalb").exists()
    assert not data.exists() or not (data / "raw").exists()


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


def test_leere_seite_wird_nicht_geschrieben_aber_als_erledigt_vermerkt(tmp_path, bestand, monkeypatch):
    """Finding 8: antwortet das Vision-Modell mit dem im Prompt verlangten
    Sentinel fuer eine leere Seite, darf kein raw/-File entstehen und die
    Seite darf nicht als Text-/Vision-Inhalt gezaehlt werden — sonst distilliert
    der Compile spaeter eine leere Seite. Sie muss aber in pages_done landen,
    sonst wird sie bei jedem Lauf erneut (kostenpflichtig) angefragt."""
    monkeypatch.setattr(docs, "extract_text", lambda page: "")
    data = tmp_path / "illico-data"

    llm = FakeLLM(answer=f"  {docs.BLANK_PAGE_SENTINEL}  \n")
    report = docs.ingest_documents(target=bestand, data=data, label="l",
                                   model="m", jobs=1, call=llm)

    assert list((data / "raw" / "l").glob("*.md")) == []
    assert report.pages_text == 0 and report.pages_vision == 0
    assert report.pages_blank == 2

    manifest = docs.load_manifest(data / docs.MANIFEST_NAME)
    for entry in manifest.values():
        assert entry["pages_done"] == [1], "leere Seite muss trotzdem als erledigt gelten"

    # Zweiter Lauf darf die leere Seite nicht erneut anfragen.
    llm.calls = 0
    report2 = docs.ingest_documents(target=bestand, data=data, label="l",
                                    model="m", jobs=1, call=llm)
    assert llm.calls == 0
    assert report2.documents_skipped == 2


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
    assert report.capped, "Bericht muss zeigen, dass die Obergrenze und nicht der Bestand den Lauf beendet hat"


def test_max_pages_wird_nicht_erreicht_capped_bleibt_falsch(tmp_path, bestand):
    """capped darf nur True sein, wenn die Obergrenze wirklich Seiten gekappt hat."""
    data = tmp_path / "illico-data"
    report = docs.ingest_documents(target=bestand, data=data, label="l",
                                   model="m", jobs=1, max_pages=100,
                                   call=FakeLLM())
    assert not report.capped


def test_max_pages_bindet_versuche_nicht_nur_erfolge(tmp_path, bestand, monkeypatch):
    """Finding 3: schlaegt jede Seite fehl, darf --max-pages trotzdem nicht
    umgangen werden. Vor dem Fix wurde budget nur bei Erfolg dekrementiert —
    bei lauter Fehlschlaegen blieb es unveraendert und jedes der zwei
    Dokumente im `bestand`-Fixture bekam das volle Budget erneut, macht
    insgesamt 2 Modellaufrufe statt der vorgegebenen 1."""
    monkeypatch.setattr(docs, "extract_text", lambda page: "")

    def boom(model, messages, system=None, max_tokens=2000, retries=3):
        raise RuntimeError("Modell kaputt")

    data = tmp_path / "illico-data"
    report = docs.ingest_documents(target=bestand, data=data, label="l",
                                   model="m", jobs=1, max_pages=1,
                                   call=boom)
    assert report.pages_failed == 1, (
        "budget muss beim Versuch abgezogen werden, nicht erst beim Erfolg — "
        f"sonst versucht jedes Dokument erneut die volle Obergrenze (war: {report.pages_failed})"
    )


def make_multi_page_pdf(n: int) -> bytes:
    """Baut ein winziges, gueltiges PDF mit `n` inhaltsleeren Seiten, die sich
    einen Content-Stream teilen. Nur die Seitenzahl zaehlt fuer diese Tests —
    Text/Rendering wird ohnehin gestubbt."""
    kids = " ".join(f"{i} 0 R" for i in range(3, 3 + n))
    content_obj = 3 + n
    parts = [b"%PDF-1.4\n",
             b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
             f"2 0 obj<</Type/Pages/Kids[{kids}]/Count {n}>>endobj\n".encode()]
    for i in range(n):
        obj_num = 3 + i
        parts.append(
            f"{obj_num} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            f"/Contents {content_obj} 0 R>>endobj\n".encode()
        )
    parts.append(f"{content_obj} 0 obj<</Length 4>>stream\nq Q\nendstream\nendobj\n".encode())
    parts.append(b"trailer<</Root 1 0 R>>\n")
    return b"".join(parts)


MULTI_PAGE_PDF = make_multi_page_pdf(3)


def test_grosse_dokumente_werden_in_chunks_verarbeitet(tmp_path, monkeypatch):
    """Finding 7: der komplette PNG-Satz eines Dokuments darf nicht auf einmal
    im Speicher liegen. Statt Speicher zu messen, prueft der Test die
    beobachtbare Konsequenz der Chunk-Aufteilung: bei jobs=1 (chunk_size=4)
    braucht ein 5-Seiten-Dokument zwei ThreadPoolExecutor-Durchlaeufe statt
    einem einzigen fuer alle 5 Seiten."""
    from concurrent.futures import ThreadPoolExecutor

    src = tmp_path / "bestand"
    src.mkdir()
    (src / "gross.pdf").write_bytes(make_multi_page_pdf(5))
    data = tmp_path / "illico-data"

    monkeypatch.setattr(docs, "extract_text", lambda page: "")
    monkeypatch.setattr(docs, "render_page_png", lambda page, dpi: b"\x89PNG-fake")

    pool_sizes = []
    real_pool = ThreadPoolExecutor

    def fake_pool_ctor(*args, **kwargs):
        pool_sizes.append(True)
        return real_pool(*args, **kwargs)

    monkeypatch.setattr(docs, "ThreadPoolExecutor", fake_pool_ctor)

    docs.ingest_documents(target=src, data=data, label="l", model="m",
                          jobs=1, call=FakeLLM())

    assert len(pool_sizes) == 2, (
        "5 Seiten bei chunk_size=jobs*4=4 muessen in zwei Chunks laufen, "
        f"nicht in einem — war {len(pool_sizes)}"
    )


def test_manifest_wird_waehrend_des_dokuments_gesichert(tmp_path, monkeypatch):
    """Finding 1: bricht der Lauf mitten in einem Dokument ab (hier: LLMAuthError
    auf Seite 2 von 3), muss die bereits erledigte Seite 1 im Manifest stehen —
    sonst zahlt ein Neustart die schon bezahlte Vision-Seite erneut, und weil
    Vision nicht deterministisch ist, invalidiert das auch noch den
    Destillat-Cache. Vor dem Fix stand save_manifest() erst NACH dem
    ThreadPoolExecutor-Block; ein Escape mitten aus dem Block (Ctrl-C,
    LLMAuthError-Re-Raise, Crash) hat pages_done nie geschrieben."""
    import illico_llm

    src = tmp_path / "bestand"
    src.mkdir()
    (src / "doc.pdf").write_bytes(MULTI_PAGE_PDF)
    data = tmp_path / "illico-data"

    monkeypatch.setattr(docs, "extract_text", lambda page: "")
    monkeypatch.setattr(docs, "render_page_png", lambda page, dpi: b"\x89PNG-fake")

    calls = {"n": 0}

    def flaky(model, messages, system=None, max_tokens=2000, retries=3):
        calls["n"] += 1
        if calls["n"] == 2:
            raise illico_llm.LLMAuthError("kein Key")
        return "# Seite\n"

    with pytest.raises(illico_llm.LLMAuthError):
        docs.ingest_documents(target=src, data=data, label="l",
                              model="m", jobs=1, call=flaky)

    manifest = docs.load_manifest(data / docs.MANIFEST_NAME)
    entry = manifest.get("l/doc.pdf")
    assert entry is not None, "das Manifest muss trotz Abbruch bereits einen Eintrag fuer das Dokument haben"
    assert entry["pages_done"], "die vor dem Abbruch fertige Seite 1 muss im Manifest stehen"
    assert 1 in entry["pages_done"]
