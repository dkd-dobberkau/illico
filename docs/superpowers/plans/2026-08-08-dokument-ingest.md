# Dokument-Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `illico-ingest documents <pfad> --label <name>` bringt PDF-Seiten als Markdown nach `raw/`, wo der bestehende Compile sie unverändert weiterverarbeitet.

**Architecture:** pypdfium2 liest jede Seite. Hat sie eine Textebene, wird der Text direkt übernommen (null LLM-Aufrufe). Ist sie ein Scan, wird sie mit 200 dpi gerendert und an ein Vision-Modell geschickt. Ein Extraktions-Cache über die PDF-Bytes sorgt dafür, dass `raw/` pro Dokumentversion genau einmal geschrieben wird — sonst würde die nichtdeterministische Vision-Ausgabe bei jedem Lauf alle Destillate invalidieren.

**Tech Stack:** Python 3.11+, pypdfium2 5.12+, Pillow, typer, rich, litellm (über `illico_llm.call_sync`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-dokument-ingest-design.md`

## Global Constraints

- **Neue Abhängigkeiten:** genau zwei — `pypdfium2>=5.12,<6.0` und `pillow>=11.0,<13.0`. Keine weiteren. Insbesondere kein PyTorch, kein PaddlePaddle, kein PyMuPDF (AGPL).
- **`illico_llm.py` wird nicht verändert.** `call_sync(model, messages, system=None, max_tokens=2000, retries=3)` nimmt bereits multimodale `messages`.
- **Kein Test geht ins Netz.** Das LLM ist immer eine Fälschung.
- **Code-Kommentare und Docstrings in ASCII-transliteriertem Deutsch** (`fuer`, `aendern`, `naechste`) — Konvention des Bestands. Markdown-Dateien nutzen echte Umlaute.
- **Jedes neue Modul muss in `pyproject.toml` unter `[tool.hatch.build.targets.wheel] only-include`.** `tests/test_packaging_completeness.py` schlägt sonst an.
- **Kein Import von Cloud-Modulen** (`illico_tenants`, `illico_cloud`, `illico_app_cloud`, `illico_cloud_compile`). `tests/test_import_boundary.py` wacht darüber.
- **Modell-Default `anthropic/claude-sonnet-5`** — erbt bewusst *nicht* `ILLICO_ANSWER_MODEL` (Haiku 4.5 liegt nicht in der Hochauflösungs-Klasse).
- Tests laufen mit `.venv-pub`: `source .venv-pub/bin/activate` vor `pytest`.

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `illico_documents.py` (neu) | Gesamte Dokumentlogik: Weiche, Extraktion, Cache, Treiber |
| `illico_ingest.py` (ändern) | Nur der `documents`-Subcommand, delegiert sofort |
| `pyproject.toml` (ändern) | Zwei Abhängigkeiten, ein `only-include`-Eintrag |
| `tests/test_documents_pure.py` (neu) | Reine Funktionen: Weiche, Slug, Frontmatter |
| `tests/test_documents_pdf.py` (neu) | pypdfium2-Anbindung, Smoke-Test mit echtem PDF |
| `tests/test_documents_routing.py` (neu) | Text-vs-Vision-Weiche mit gefälschtem LLM |
| `tests/test_documents_cache.py` (neu) | `_documents.json`, Wiederaufnahme nach Teilausfall |
| `tests/test_documents_e2e.py` (neu) | CLI und Zusammenspiel mit `illico-compile` |
| `README.md`, `README.en.md` (ändern) | Neuer Abschnitt zum Dokument-Ingest |

`illico_documents.py` bleibt eine Datei: die Bausteine teilen sich Konstanten und der Treiber ruft sie alle. Erwartete Größe ~280 Zeilen, damit innerhalb der 300–400-Zeilen-Grenze.

---

## Task 1: Abhängigkeiten, Modulgerüst und die reinen Funktionen

**Files:**
- Modify: `pyproject.toml`
- Create: `illico_documents.py`
- Test: `tests/test_documents_pure.py`

**Interfaces:**
- Consumes: `illico_inventory.slugify(text: str) -> str` (bestehend)
- Produces:
  - `is_text_sufficient(text: str, threshold: int) -> bool`
  - `document_slug(pdf_path: Path, root: Path) -> str`
  - `page_filename(slug: str, page_no: int, pages_total: int) -> str`
  - `build_page_frontmatter(title: str, rel_source: str, page_no: int, label: str, language: str | None) -> str`
  - Konstanten `DEFAULT_MODEL`, `DEFAULT_DPI`, `DEFAULT_TEXT_THRESHOLD`, `VISION_MAX_TOKENS`

- [ ] **Step 1: Abhängigkeiten eintragen**

In `pyproject.toml`, in der `dependencies`-Liste nach `"langdetect>=1.0.9,<2.0",` ergänzen:

```toml
  "pypdfium2>=5.12,<6.0",
  "pillow>=11.0,<13.0",
```

Und in `[tool.hatch.build.targets.wheel] only-include` die Liste um das neue Modul erweitern — hinter `"illico_inventory.py",`:

```toml
  "illico_documents.py",
```

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

Create `tests/test_documents_pure.py`:

```python
"""Reine Funktionen des Dokument-Ingests: Weiche, Namensbildung, Frontmatter."""
from pathlib import Path

import illico_documents as docs


def test_leere_seite_ist_nicht_ausreichend():
    assert docs.is_text_sufficient("", 200) is False
    assert docs.is_text_sufficient("   \n\t  ", 200) is False


def test_schwelle_wird_exakt_eingehalten():
    assert docs.is_text_sufficient("x" * 199, 200) is False
    assert docs.is_text_sufficient("x" * 200, 200) is True
    assert docs.is_text_sufficient("x" * 201, 200) is True


def test_umgebender_whitespace_zaehlt_nicht_mit():
    assert docs.is_text_sufficient("  " + "x" * 199 + "  ", 200) is False


def test_slug_unterscheidet_gleichnamige_dateien_in_unterordnern():
    root = Path("/bestand")
    a = docs.document_slug(Path("/bestand/2024/bericht.pdf"), root)
    b = docs.document_slug(Path("/bestand/2025/bericht.pdf"), root)
    assert a != b
    assert "bericht" in a and "bericht" in b


def test_slug_ist_ueber_laeufe_stabil():
    root = Path("/bestand")
    p = Path("/bestand/handbuch.pdf")
    assert docs.document_slug(p, root) == docs.document_slug(p, root)


def test_seitennummer_wird_auf_dokumentbreite_gepolstert():
    assert docs.page_filename("handbuch", 7, 9) == "handbuch--s7.md"
    assert docs.page_filename("handbuch", 7, 312) == "handbuch--s007.md"
    assert docs.page_filename("handbuch", 312, 312) == "handbuch--s312.md"


def test_frontmatter_traegt_label_als_domain():
    fm = docs.build_page_frontmatter(
        title="Betriebshandbuch", rel_source="a/b.pdf",
        page_no=47, label="handbuecher", language="de",
    )
    assert 'domain: "handbuecher"' in fm
    assert 'language: "de"' in fm
    assert "Seite 47" in fm
    assert "#page=47" in fm
    assert fm.startswith("---\n") and fm.rstrip().endswith("---")


def test_frontmatter_ohne_sprache_laesst_das_feld_weg():
    fm = docs.build_page_frontmatter(
        title="T", rel_source="b.pdf", page_no=1, label="l", language=None,
    )
    assert "language:" not in fm
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_pure.py -q
```

Erwartet: Collection-Error, `ModuleNotFoundError: No module named 'illico_documents'`.

- [ ] **Step 4: Modul mit den reinen Funktionen anlegen**

Create `illico_documents.py`:

```python
"""illico_documents — Dokumente (PDF) als Markdown nach raw/ bringen.

Text zuerst, Vision als Rueckfall: Seiten mit eingebetteter Textebene werden
direkt uebernommen und kosten nichts, Scans werden gerendert und an ein
Vision-Modell geschickt.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from illico_inventory import slugify

DEFAULT_MODEL = "anthropic/claude-sonnet-5"
DEFAULT_DPI = 200
DEFAULT_TEXT_THRESHOLD = 200
VISION_MAX_TOKENS = 4000


def is_text_sufficient(text: str, threshold: int) -> bool:
    """True, wenn die Seite genug eingebetteten Text hat, um Vision zu sparen."""
    return len(text.strip()) >= threshold


def document_slug(pdf_path: Path, root: Path) -> str:
    """Slug aus dem Pfad RELATIV zur Wurzel, plus Kurz-Hash.

    Relativ, weil sonst 2024/bericht.pdf und 2025/bericht.pdf dieselbe Datei
    ueberschrieben. Der Hash haengt an, weil slugify() bei 60 Zeichen abschneidet
    und lange Pfade sonst kollidieren koennten. Er ist aus dem Pfad abgeleitet,
    also ueber Laeufe hinweg stabil.
    """
    rel = pdf_path.relative_to(root)
    stem = str(rel.with_suffix(""))
    digest = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:6]
    return f"{slugify(stem)}-{digest}"


def page_filename(slug: str, page_no: int, pages_total: int) -> str:
    """Dateiname einer Seite, Seitenzahl auf die Dokumentbreite gepolstert."""
    width = len(str(pages_total))
    return f"{slug}--s{page_no:0{width}d}.md"


def build_page_frontmatter(
    title: str,
    rel_source: str,
    page_no: int,
    label: str,
    language: str | None = None,
) -> str:
    """YAML-Frontmatter einer Dokumentseite.

    `domain` ist das Label — daran haengen Ablagepfad, --only-domains und im
    Cloud-Overlay die Mandantentrennung.
    """
    date = datetime.now().strftime("%Y-%m-%d")
    safe_title = title.replace('"', "'")
    lang_line = f'language: "{language}"\n' if language else ""
    return f"""---
title: "{safe_title} — Seite {page_no}"
source_url: "file://{rel_source}#page={page_no}"
domain: "{label}"
crawled: "{date}"
{lang_line}---

"""
```

- [ ] **Step 5: Test laufen lassen, Erfolg bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_pure.py -q
```

Erwartet: `8 passed`.

- [ ] **Step 6: Abhängigkeiten installieren und die Gesamtsuite grün halten**

```bash
source .venv-pub/bin/activate && pip install -q -e ".[test]" && pytest -q
```

Erwartet: alle bisherigen Tests plus die 8 neuen bestehen. `test_packaging_completeness.py` muss grün sein — schlägt es fehl, fehlt der `only-include`-Eintrag aus Step 1.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml illico_documents.py tests/test_documents_pure.py
git commit -m "feat(documents): Modulgeruest, Weiche und Namensbildung

Slug aus dem Pfad relativ zur Wurzel plus Kurz-Hash: gleichnamige Dateien in
verschiedenen Unterordnern duerfen nicht dieselbe raw/-Datei ueberschreiben,
und slugify() schneidet bei 60 Zeichen ab."
```

---

## Task 2: PDF lesen — Text extrahieren und Seiten rendern

**Files:**
- Modify: `illico_documents.py`
- Create: `tests/fixtures/__init__.py` (leer, damit der Ordner mitgeht)
- Create: `tests/test_documents_pdf.py`

**Interfaces:**
- Consumes: nichts aus Task 1 außer `DEFAULT_DPI`
- Produces:
  - `open_document(path: Path, password: str | None = None) -> pdfium.PdfDocument`
  - `extract_text(page) -> str`
  - `render_page_png(page, dpi: int) -> bytes`
  - `document_title(pdf, fallback: str) -> str`

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

Das Fixture-PDF wird im Test selbst erzeugt — ein von Hand geschriebenes, minimal gültiges PDF mit Textebene. Das erspart eine Binärdatei im Repo.

Create `tests/test_documents_pdf.py`:

```python
"""Anbindung an pypdfium2. Ein Smoke-Test mit einem echten Mini-PDF.

Geprueft wird die Verdrahtung, nicht pdfium selbst: dass wir die richtigen
Methoden aufrufen und Bytes zurueckbekommen.
"""
from pathlib import Path

import pytest

import illico_documents as docs

# Von Hand geschriebenes, gueltiges PDF mit einer Seite und Textebene.
MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
5 0 obj<</Length 68>>stream
BT /F1 24 Tf 72 700 Td (Hallo Illico aus einem PDF) Tj ET
endstream
endobj
trailer<</Root 1 0 R>>
"""


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    p = tmp_path / "mini.pdf"
    p.write_bytes(MINIMAL_PDF)
    return p


def test_textebene_wird_gelesen(pdf_path: Path):
    pdf = docs.open_document(pdf_path)
    text = docs.extract_text(pdf[0])
    assert "Hallo Illico" in text


def test_seite_rendert_zu_png_bytes(pdf_path: Path):
    pdf = docs.open_document(pdf_path)
    png = docs.render_page_png(pdf[0], dpi=100)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 100


def test_seitenzahl_stimmt(pdf_path: Path):
    assert len(docs.open_document(pdf_path)) == 1


def test_defektes_pdf_wirft_pdfiumerror(tmp_path: Path):
    import pypdfium2 as pdfium

    bad = tmp_path / "kaputt.pdf"
    bad.write_bytes(b"das ist kein PDF")
    with pytest.raises(pdfium.PdfiumError):
        docs.open_document(bad)


def test_titel_faellt_auf_den_dateinamen_zurueck(pdf_path: Path):
    pdf = docs.open_document(pdf_path)
    assert docs.document_title(pdf, fallback="mini") == "mini"
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_pdf.py -q
```

Erwartet: `AttributeError: module 'illico_documents' has no attribute 'open_document'`.

- [ ] **Step 3: Die pdfium-Anbindung implementieren**

In `illico_documents.py` die Importe ergänzen (`io`, `pypdfium2`) und die vier Funktionen nach `build_page_frontmatter` anfügen:

```python
import io

import pypdfium2 as pdfium


def open_document(path: Path, password: str | None = None) -> pdfium.PdfDocument:
    """Oeffnet ein PDF. Wirft pdfium.PdfiumError bei defekten oder
    passwortgeschuetzten Dateien — der Treiber faengt das pro Dokument ab."""
    return pdfium.PdfDocument(path, password=password)


def extract_text(page) -> str:
    """Eingebetteter Text einer Seite. Leer bei Scans."""
    textpage = page.get_textpage()
    try:
        return textpage.get_text_bounded()
    finally:
        textpage.close()


def render_page_png(page, dpi: int = DEFAULT_DPI) -> bytes:
    """Rendert eine Seite zu PNG-Bytes.

    200 dpi ergeben bei A4 1654x2338 px und bleiben damit unter der Grenze von
    2576 px an der langen Kante, ab der das Modell herunterskalieren wuerde.
    PNG statt JPEG: die Token-Kosten haengen an den Abmessungen, nicht an der
    Dateigroesse, und Artefakte auf Text bringen nichts.
    """
    bitmap = page.render(scale=dpi / 72)
    buffer = io.BytesIO()
    bitmap.to_pil().save(buffer, format="PNG")
    return buffer.getvalue()


def document_title(pdf: pdfium.PdfDocument, fallback: str) -> str:
    """Titel aus den PDF-Metadaten, sonst der uebergebene Fallback."""
    try:
        title = (pdf.get_metadata_dict() or {}).get("Title", "")
    except Exception:
        title = ""
    return title.strip() or fallback
```

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_pdf.py -q
```

Erwartet: `5 passed`.

Schlägt `test_textebene_wird_gelesen` fehl, weil `get_text_bounded()` nicht existiert: gegen die installierte Version prüfen mit
`python -c "import pypdfium2 as p; print([m for m in dir(p.PdfTextPage) if m.startswith('get_text')])"`.
Verifiziert wurde gegen 5.12.1.

- [ ] **Step 5: Commit**

```bash
git add illico_documents.py tests/test_documents_pdf.py
git commit -m "feat(documents): Text extrahieren und Seiten zu PNG rendern

200 dpi, weil A4 damit 1654x2338 px ergibt und unter der 2576-px-Grenze
bleibt, ab der das Modell herunterskalieren wuerde."
```

---

## Task 3: Die Weiche — eine Seite zu Markdown

**Files:**
- Modify: `illico_documents.py`
- Create: `tests/test_documents_routing.py`

**Interfaces:**
- Consumes: `is_text_sufficient`, `extract_text`, `render_page_png`, `VISION_MAX_TOKENS`, `DEFAULT_DPI`
- Produces:
  - `@dataclass PreparedPage` mit `page_no: int`, `markdown: str | None`, `png: bytes | None`
  - `prepare_page(page, page_no: int, threshold: int, force_vision: bool, dpi: int) -> PreparedPage`
  - `finish_page(prepared: PreparedPage, model: str, call) -> tuple[str, bool]` — Rückgabe `(markdown, ging_ueber_vision)`
  - `vision_markdown(png: bytes, model: str, call) -> str`
  - `PageExtractionError`
  - `VISION_PROMPT: str`

`call` ist immer `illico_llm.call_sync` und wird nur in Tests ersetzt. Signatur: `call(model, messages, system=None, max_tokens=…, retries=…) -> str`.

**Warum zwei Funktionen statt einer:** `prepare_page` fasst nur PDF an und
bleibt im Hauptthread (PDFium ist nicht ohne Weiteres threadsicher);
`finish_page` macht nur den Netzaufruf und läuft im Pool. Der Treiber in Task 5
setzt beide zusammen, statt die Weiche ein zweites Mal nachzubauen.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

Create `tests/test_documents_routing.py`:

```python
"""Die Weiche: Textseiten kosten nichts, Scans gehen ueber Vision.

Gestubbt wird an der Naht `extract_text`. Ob pdfium bei einem echten Scan
wirklich leeren Text liefert, ist pdfiums Sache und nicht unsere.
"""
import illico_documents as docs


class FakeLLM:
    """Zaehlt Aufrufe und liefert festes Markdown."""

    def __init__(self, answer="# Aus dem Bild\n\nText.\n"):
        self.calls = 0
        self.answer = answer
        self.last_messages = None

    def __call__(self, model, messages, system=None, max_tokens=2000, retries=3):
        self.calls += 1
        self.last_messages = messages
        return self.answer


class FakePage:
    """Ersetzt eine pdfium-Seite. Wird nur durchgereicht, nie benutzt."""


def _patch(monkeypatch, text: str):
    monkeypatch.setattr(docs, "extract_text", lambda page: text)
    monkeypatch.setattr(docs, "render_page_png", lambda page, dpi: b"\x89PNG-fake")


def _run(monkeypatch, text, llm, threshold=200, force_vision=False):
    """Beide Haelften nacheinander — so setzt der Treiber sie auch zusammen."""
    _patch(monkeypatch, text)
    prepared = docs.prepare_page(FakePage(), page_no=1, threshold=threshold,
                                 force_vision=force_vision, dpi=200)
    return prepared, docs.finish_page(prepared, model="m", call=llm)


def test_textseite_kostet_keinen_llm_aufruf(monkeypatch):
    llm = FakeLLM()
    prepared, (markdown, via_vision) = _run(monkeypatch, "x" * 500, llm)

    assert llm.calls == 0
    assert via_vision is False
    assert markdown.strip() == "x" * 500
    assert prepared.png is None, "eine Textseite darf gar nicht erst gerendert werden"


def test_scanseite_geht_genau_einmal_ans_modell(monkeypatch):
    llm = FakeLLM()
    prepared, (markdown, via_vision) = _run(monkeypatch, "", llm)

    assert llm.calls == 1
    assert via_vision is True
    assert "Aus dem Bild" in markdown
    assert prepared.markdown is None


def test_force_vision_schickt_auch_textseiten_ans_modell(monkeypatch):
    llm = FakeLLM()
    _, (_, via_vision) = _run(monkeypatch, "x" * 500, llm, force_vision=True)

    assert llm.calls == 1
    assert via_vision is True


def test_schwelle_verschiebt_die_weiche(monkeypatch):
    llm = FakeLLM()
    _run(monkeypatch, "x" * 300, llm, threshold=500)

    assert llm.calls == 1, "300 Zeichen unter Schwelle 500 muessen ueber Vision gehen"


def test_prepare_page_macht_keinen_netzaufruf(monkeypatch):
    """Die Trennung ist der Zweck: prepare_page laeuft im Hauptthread."""
    _patch(monkeypatch, "")
    llm = FakeLLM()

    docs.prepare_page(FakePage(), page_no=1, threshold=200,
                      force_vision=False, dpi=200)

    assert llm.calls == 0


def test_seitennummer_ueberlebt_die_vorbereitung(monkeypatch):
    _patch(monkeypatch, "")
    prepared = docs.prepare_page(FakePage(), page_no=47, threshold=200,
                                 force_vision=False, dpi=200)
    assert prepared.page_no == 47


def test_bild_wird_als_data_uri_geschickt(monkeypatch):
    llm = FakeLLM()
    _run(monkeypatch, "", llm)

    content = llm.last_messages[0]["content"]
    image_block = next(b for b in content if b["type"] == "image_url")
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")


def test_leere_modellantwort_gilt_als_fehlschlag(monkeypatch):
    llm = FakeLLM(answer="   \n  ")

    with pytest.raises(docs.PageExtractionError):
        _run(monkeypatch, "", llm)
```

Am Dateianfang zusätzlich `import pytest` ergänzen.

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_routing.py -q
```

Erwartet: `AttributeError: module 'illico_documents' has no attribute 'prepare_page'`.

- [ ] **Step 3: Weiche und Vision-Aufruf implementieren**

In `illico_documents.py` `import base64` ergänzen und anfügen:

```python
class PageExtractionError(Exception):
    """Eine einzelne Seite konnte nicht extrahiert werden."""


VISION_PROMPT = """Gib den Inhalt dieser Dokumentseite als Markdown wieder.

- Nur der Seiteninhalt, keine Einleitung und kein Nachwort.
- Ueberschriften als Markdown-Ueberschriften, Tabellen als Markdown-Tabellen,
  Listen als Listen.
- Abbildungen und Diagramme in einem Satz beschreiben, in eckigen Klammern.
- Kopf- und Fusszeilen, Seitenzahlen und Wasserzeichen weglassen.
- Schreibe in der Sprache der Seite.
- Ist die Seite leer, antworte mit genau: (leere Seite)
"""


def vision_markdown(png: bytes, model: str, call) -> str:
    """Schickt ein Seitenbild ans Modell und gibt Markdown zurueck."""
    encoded = base64.b64encode(png).decode("ascii")
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            {"type": "text", "text": VISION_PROMPT},
        ],
    }]
    return call(model, messages, max_tokens=VISION_MAX_TOKENS)


@dataclass
class PreparedPage:
    """Eine Seite nach dem PDF-Teil der Arbeit.

    Genau eines von `markdown` und `png` ist gesetzt: entweder die Textebene
    hat gereicht, oder die Seite muss noch ans Modell.
    """
    page_no: int
    markdown: str | None = None
    png: bytes | None = None


def prepare_page(
    page,
    page_no: int,
    threshold: int = DEFAULT_TEXT_THRESHOLD,
    force_vision: bool = False,
    dpi: int = DEFAULT_DPI,
) -> PreparedPage:
    """Der PDF-Teil: Textebene lesen oder rendern. Kein Netzaufruf.

    Laeuft im Hauptthread, weil PDFium nicht ohne Weiteres threadsicher ist.
    """
    if not force_vision:
        text = extract_text(page)
        if is_text_sufficient(text, threshold):
            return PreparedPage(page_no=page_no, markdown=text.strip() + "\n")
    return PreparedPage(page_no=page_no, png=render_page_png(page, dpi=dpi))


def finish_page(prepared: PreparedPage, model: str, call=None) -> tuple[str, bool]:
    """Der Netz-Teil: fertiges Markdown durchreichen oder Vision aufrufen.

    Liefert (markdown, ging_ueber_vision). Laeuft im Pool. Wirft
    PageExtractionError, wenn das Modell nichts Brauchbares liefert — die Seite
    darf dann nicht als erledigt im Cache landen.
    """
    if prepared.markdown is not None:
        return prepared.markdown, False

    if call is None:
        import illico_llm
        call = illico_llm.call_sync

    markdown = vision_markdown(prepared.png, model, call)
    if not markdown or not markdown.strip():
        raise PageExtractionError("Modell lieferte eine leere Antwort")
    return markdown.strip() + "\n", True
```

`from dataclasses import dataclass` am Modulanfang ergänzen.

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_routing.py -q
```

Erwartet: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add illico_documents.py tests/test_documents_routing.py
git commit -m "feat(documents): Weiche zwischen Textebene und Vision

In zwei Haelften geschnitten: prepare_page fasst nur das PDF an und bleibt im
Hauptthread (PDFium ist nicht threadsicher), finish_page macht nur den
Netzaufruf und laeuft spaeter im Pool. So braucht der Treiber die Weiche nicht
nachzubauen.

Eine leere Modellantwort wirft PageExtractionError statt durchzurutschen —
sonst landete die Seite als erledigt im Cache und wuerde nie nachgeholt."
```

---

## Task 4: Extraktions-Cache

**Files:**
- Modify: `illico_documents.py`
- Create: `tests/test_documents_cache.py`

**Interfaces:**
- Consumes: nichts aus früheren Tasks
- Produces:
  - `file_hash(path: Path) -> str` — liefert `"sha256:<hex>"`
  - `load_manifest(path: Path) -> dict`
  - `save_manifest(path: Path, manifest: dict) -> None`
  - `pending_pages(entry: dict | None, pages_total: int) -> list[int]` — 1-basierte Seitenzahlen, die noch fehlen
  - Konstante `MANIFEST_NAME = "_documents.json"`

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

Create `tests/test_documents_cache.py`:

```python
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
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_cache.py -q
```

Erwartet: `AttributeError: module 'illico_documents' has no attribute 'file_hash'`.

- [ ] **Step 3: Cache implementieren**

In `illico_documents.py` `import json` ergänzen und anfügen:

```python
MANIFEST_NAME = "_documents.json"


def file_hash(path: Path) -> str:
    """SHA-256 ueber die Datei-Bytes, in 1-MiB-Bloecken."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def load_manifest(path: Path) -> dict:
    """Laedt das Manifest. Fehlend oder kaputt heisst leer — ein beschaedigtes
    Manifest kostet einen teuren Neulauf, darf ihn aber nicht verhindern."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def pending_pages(entry: dict | None, pages_total: int) -> list[int]:
    """Noch fehlende Seiten, 1-basiert.

    Weicht die gespeicherte Seitenzahl ab, ist der Eintrag unbrauchbar und
    alles wird neu geholt.
    """
    if not entry or entry.get("pages_total") != pages_total:
        return list(range(1, pages_total + 1))
    done = set(entry.get("pages_done", []))
    return [n for n in range(1, pages_total + 1) if n not in done]
```

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_cache.py -q
```

Erwartet: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add illico_documents.py tests/test_documents_cache.py
git commit -m "feat(documents): Extraktions-Cache ueber die PDF-Bytes

pages_done macht einen Teilausfall billig: bricht Seite 150 von 312 ab, holt
der naechste Lauf nur die fehlenden nach."
```

---

## Task 5: Treiber

**Files:**
- Modify: `illico_documents.py`
- Modify: `tests/test_documents_cache.py` (Treiber-Tests anfügen)

**Interfaces:**
- Consumes: alles aus Task 1–4
- Produces:
  - `@dataclass IngestReport` mit `documents`, `documents_skipped`, `non_pdf_skipped`, `pages_text`, `pages_vision`, `pages_failed`, `errors: list[str]`
  - `find_pdfs(target: Path) -> tuple[Path, list[Path], int]` — `(root, pdfs, uebersprungene_nicht_pdf)`
  - `ingest_documents(target, data, label, model, jobs, fresh, max_pages, threshold, force_vision, call) -> IngestReport`

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

An `tests/test_documents_cache.py` anfügen:

```python
import pypdfium2 as pdfium
import pytest

from test_documents_pdf import MINIMAL_PDF
from test_documents_routing import FakeLLM


@pytest.fixture
def bestand(tmp_path: Path) -> Path:
    """Ordner mit zwei PDFs und einer Nicht-PDF-Datei."""
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
    data = tmp_path / "illico-data"
    report = docs.ingest_documents(
        target=bestand, data=data, label="handbuecher",
        model="m", jobs=1, call=FakeLLM(),
    )
    written = sorted((data / "raw" / "handbuecher").glob("*.md"))
    assert len(written) == 2
    assert report.documents == 2
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
        def __call__(self, model, messages, system=None, max_tokens=2000, retries=3):
            self.calls += 1
            if self.calls == 1:
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
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_cache.py -q
```

Erwartet: `AttributeError: module 'illico_documents' has no attribute 'find_pdfs'`.

- [ ] **Step 3: Treiber implementieren**

In `illico_documents.py` ergänzen (`dataclass`, `ThreadPoolExecutor`):

```python
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


@dataclass
class IngestReport:
    documents: int = 0
    documents_skipped: int = 0
    non_pdf_skipped: int = 0
    pages_text: int = 0
    pages_vision: int = 0
    pages_failed: int = 0
    errors: list[str] = field(default_factory=list)


def find_pdfs(target: Path) -> tuple[Path, list[Path], int]:
    """Sucht rekursiv nach *.pdf. Liefert (wurzel, pdfs, uebersprungene).

    Die Wurzel ist Bezugspunkt fuer den Slug — bei einer Einzeldatei ihr
    Elternverzeichnis.
    """
    if target.is_file():
        return target.parent, [target], 0
    pdfs, skipped = [], 0
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".pdf":
            pdfs.append(path)
        else:
            skipped += 1
    return target, pdfs, skipped


def ingest_documents(
    target: Path,
    data: Path,
    label: str,
    model: str = DEFAULT_MODEL,
    jobs: int = 4,
    fresh: bool = False,
    max_pages: int | None = None,
    threshold: int = DEFAULT_TEXT_THRESHOLD,
    force_vision: bool = False,
    dpi: int = DEFAULT_DPI,
    call=None,
) -> IngestReport:
    """Extrahiert alle PDFs unter `target` nach data/raw/<label>/.

    pdfium laeuft bewusst einstraengig: Textextraktion und Rendering passieren
    sequenziell im Hauptthread (beides CPU-gebunden und schnell), nur die
    Vision-Aufrufe werden ueber `jobs` gefaechert. PDFium ist nicht ohne
    Weiteres threadsicher, und die Wartezeit liegt ohnehin im Netz.
    """
    if call is None:
        import illico_llm
        call = illico_llm.call_sync

    from illico_ingest import detect_language

    root, pdfs, non_pdf = find_pdfs(target)
    report = IngestReport(non_pdf_skipped=non_pdf)

    out_dir = data / "raw" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = data / MANIFEST_NAME
    manifest = {} if fresh else load_manifest(manifest_path)
    budget = max_pages

    for pdf_path in pdfs:
        rel_source = str(pdf_path.relative_to(root))
        try:
            digest = file_hash(pdf_path)
            pdf = open_document(pdf_path)
            pages_total = len(pdf)
        except Exception as exc:
            report.errors.append(f"{rel_source}: {exc}")
            continue

        entry = manifest.get(digest)
        todo = pending_pages(entry, pages_total)
        if not todo:
            report.documents_skipped += 1
            continue
        if budget is not None:
            todo = todo[:max(0, budget)]
            if not todo:
                break

        slug = document_slug(pdf_path, root)
        title = document_title(pdf, fallback=pdf_path.stem)
        done = set((entry or {}).get("pages_done", []))

        # Sequenziell im Hauptthread: PDF anfassen. PDFium ist nicht ohne
        # Weiteres threadsicher, und der Teil ist CPU-gebunden und schnell.
        prepared: list[PreparedPage] = []
        for page_no in todo:
            try:
                prepared.append(prepare_page(
                    pdf[page_no - 1], page_no=page_no, threshold=threshold,
                    force_vision=force_vision, dpi=dpi,
                ))
            except Exception as exc:
                report.errors.append(f"{rel_source} Seite {page_no}: {exc}")
                report.pages_failed += 1

        # Gefaechert: nur die Netzaufrufe. Seiten mit Textebene reicht
        # finish_page unveraendert durch, sie kosten hier nichts.
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futures = [(p, pool.submit(finish_page, p, model, call))
                       for p in prepared]
            for item, future in futures:
                page_no = item.page_no
                try:
                    markdown, via_vision = future.result()
                except illico_llm.LLMAuthError:
                    raise
                except Exception as exc:
                    report.errors.append(f"{rel_source} Seite {page_no}: {exc}")
                    report.pages_failed += 1
                    continue

                language = detect_language(None, markdown)
                frontmatter = build_page_frontmatter(
                    title=title, rel_source=rel_source, page_no=page_no,
                    label=label, language=language,
                )
                name = page_filename(slug, page_no, pages_total)
                (out_dir / name).write_text(frontmatter + markdown, encoding="utf-8")

                done.add(page_no)
                if via_vision:
                    report.pages_vision += 1
                else:
                    report.pages_text += 1
                if budget is not None:
                    budget -= 1

        report.documents += 1
        manifest[digest] = {
            "source": rel_source, "label": label,
            "pages_total": pages_total, "pages_done": sorted(done),
        }
        save_manifest(manifest_path, manifest)

    return report
```

Zusätzlich `import illico_llm` am Modulanfang ergänzen (für `LLMAuthError` im `except`).

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_cache.py -q
```

Erwartet: alle Tests der Datei bestehen (8 aus Task 4 plus 9 neue).

- [ ] **Step 5: Gesamtsuite prüfen**

```bash
source .venv-pub/bin/activate && pytest -q
```

Erwartet: alles grün.

- [ ] **Step 6: Commit**

```bash
git add illico_documents.py tests/test_documents_cache.py
git commit -m "feat(documents): Treiber mit Cache, Fehlertoleranz und Bilanz

pdfium laeuft einstraengig — Textextraktion und Rendering sequenziell im
Hauptthread, nur die Vision-Aufrufe gefaechert. PDFium ist nicht ohne Weiteres
threadsicher, und die Wartezeit liegt im Netz."
```

---

## Task 6: CLI-Subcommand, README, Integration mit dem Compile

**Files:**
- Modify: `illico_ingest.py` (nach `migrate_lang_cmd`, ab Zeile ~830)
- Modify: `README.md`, `README.en.md`
- Create: `tests/test_documents_e2e.py`

**Interfaces:**
- Consumes: `illico_documents.ingest_documents`, `IngestReport`, Konstanten
- Produces: den Subcommand `documents` auf der bestehenden typer-App

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

Create `tests/test_documents_e2e.py`:

```python
"""CLI-Ebene und Zusammenspiel mit dem Compile."""
from pathlib import Path

from typer.testing import CliRunner

import illico_compile
import illico_documents as docs
import illico_ingest
from test_documents_pdf import MINIMAL_PDF
from test_documents_routing import FakeLLM

runner = CliRunner()


def _bestand(tmp_path: Path) -> Path:
    src = tmp_path / "bestand"
    src.mkdir()
    (src / "handbuch.pdf").write_bytes(MINIMAL_PDF)
    return src


def test_cli_legt_seiten_unter_dem_label_ab(tmp_path, monkeypatch):
    src = _bestand(tmp_path)
    data = tmp_path / "illico-data"
    monkeypatch.setattr(docs, "vision_markdown", lambda png, model, call: "# X\n")

    result = runner.invoke(illico_ingest.app, [
        "documents", str(src), "--data", str(data),
        "--label", "handbuecher", "--jobs", "1",
    ])

    assert result.exit_code == 0, result.output
    assert list((data / "raw" / "handbuecher").glob("*.md"))


def test_cli_meldet_die_bilanz(tmp_path, monkeypatch):
    src = _bestand(tmp_path)
    monkeypatch.setattr(docs, "vision_markdown", lambda png, model, call: "# X\n")

    result = runner.invoke(illico_ingest.app, [
        "documents", str(src), "--data", str(tmp_path / "d"),
        "--label", "l", "--jobs", "1",
    ])

    assert "Textebene" in result.output
    assert "Vision" in result.output


def test_cli_bricht_ohne_dateien_ab(tmp_path):
    leer = tmp_path / "leer"
    leer.mkdir()
    result = runner.invoke(illico_ingest.app, [
        "documents", str(leer), "--data", str(tmp_path / "d"), "--label", "l",
    ])
    assert result.exit_code == 1
    assert "Keine PDF" in result.output


def test_erzeugte_seiten_lassen_sich_kompilieren(tmp_path, monkeypatch):
    """Der eigentliche Zweck: was hier rauskommt, muss der Compile fressen."""
    from test_compile_incremental_e2e import ScriptedLLM

    src = _bestand(tmp_path)
    data = tmp_path / "illico-data"
    docs.ingest_documents(target=src, data=data, label="handbuecher",
                          model="m", jobs=1, call=FakeLLM())

    monkeypatch.setattr(illico_compile, "call_llm", ScriptedLLM())
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    result = runner.invoke(illico_compile.app,
                           ["--data", str(data), "--jobs", "1"])

    assert result.exit_code == 0, result.output
    assert (data / "wiki" / "_index.md").exists()


def test_only_domains_trifft_genau_das_label(tmp_path, monkeypatch):
    from test_compile_incremental_e2e import ScriptedLLM

    src = _bestand(tmp_path)
    data = tmp_path / "illico-data"
    docs.ingest_documents(target=src, data=data, label="handbuecher",
                          model="m", jobs=1, call=FakeLLM())

    monkeypatch.setattr(illico_compile, "call_llm", ScriptedLLM())
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    result = runner.invoke(illico_compile.app, [
        "--data", str(data), "--jobs", "1", "--only-domains", "handbuecher",
    ])

    assert result.exit_code == 0, result.output
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_e2e.py -q
```

Erwartet: `Error: No such command 'documents'` bzw. exit code 2.

- [ ] **Step 3: Subcommand implementieren**

An `illico_ingest.py` anfügen (nach `migrate_lang_cmd`):

```python
@app.command()
def documents(
    target: Path = typer.Argument(..., help="Verzeichnis oder einzelne PDF-Datei"),
    label: str = typer.Option(..., "--label", help="Herkunftsname; wird zur `domain:` und zum Ablagepfad"),
    data: Path = typer.Option(Path(os.environ.get("ILLICO_DATA", "./illico-data")), "--data", "-d", help="Illico-Datenverzeichnis"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Modell fuer die Vision-Extraktion (default: anthropic/claude-sonnet-5)"),
    jobs: int = typer.Option(4, "--jobs", "-j", help="Parallele LLM-Aufrufe"),
    fresh: bool = typer.Option(False, "--fresh", help="Extraktions-Cache ignorieren"),
    max_pages: Optional[int] = typer.Option(None, "--max-pages", help="Harte Obergrenze neu extrahierter Seiten ueber den ganzen Lauf"),
    text_threshold: int = typer.Option(200, "--text-threshold", help="Ab wie vielen Zeichen eine Seite als Textseite gilt"),
    force_vision: bool = typer.Option(False, "--force-vision", help="Weiche abschalten, jede Seite ueber Vision"),
):
    """
    Extrahiert PDFs zu Markdown-Seiten unter raw/<label>/.

    Seiten mit Textebene werden direkt uebernommen, Scans ueber ein
    Vision-Modell gelesen.
    """
    import illico_documents

    console.print()
    console.rule("[bold blue]ILLICO DOCUMENTS[/bold blue]")

    if not target.exists():
        console.print(f"[red]✗ {target} existiert nicht.[/red]")
        raise typer.Exit(1)

    root, pdfs, skipped = illico_documents.find_pdfs(target)
    if not pdfs:
        console.print(f"[red]✗ Keine PDF-Dateien unter {target} gefunden.[/red]")
        raise typer.Exit(1)

    effective_model = model or illico_documents.DEFAULT_MODEL
    console.print(f"  Quelle:  [cyan]{target}[/cyan]")
    console.print(f"  Label:   [cyan]{label}[/cyan]")
    console.print(f"  Modell:  [cyan]{effective_model}[/cyan]")
    console.print(f"  Dateien: [cyan]{len(pdfs)} PDF[/cyan]")
    if skipped:
        console.print(f"  [dim]{skipped} Nicht-PDF-Dateien uebersprungen[/dim]")
    console.print()

    try:
        report = illico_documents.ingest_documents(
            target=target, data=data, label=label, model=effective_model,
            jobs=jobs, fresh=fresh, max_pages=max_pages,
            threshold=text_threshold, force_vision=force_vision,
        )
    except illico_llm.LLMAuthError as exc:
        console.print(f"[red]✗ LLM authentication failed: {exc}[/red]")
        console.print("  Check your provider API key.")
        raise typer.Exit(1)

    console.print()
    console.rule("[bold green]Fertig[/bold green]")
    console.print(f"  Dokumente:   [cyan]{report.documents}[/cyan]"
                  f" ({report.documents_skipped} unveraendert uebersprungen)")
    console.print(f"  Aus Textebene: [green]{report.pages_text}[/green] Seiten")
    console.print(f"  Ueber Vision:  [yellow]{report.pages_vision}[/yellow] Seiten")
    if report.pages_failed:
        console.print(f"  [yellow]⚠ {report.pages_failed} Seiten ohne Ergebnis[/yellow]"
                      " — der naechste Lauf versucht sie erneut.")
    for message in report.errors[:10]:
        console.print(f"  [red]✗[/red] {message}")
    console.print()
    console.print("  Naechster Schritt: [cyan]illico-compile[/cyan]")
```

Am Kopf von `illico_ingest.py` prüfen, dass `illico_llm` importiert ist; falls nicht, ergänzen.

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

```bash
source .venv-pub/bin/activate && pytest tests/test_documents_e2e.py -q
```

Erwartet: `5 passed`.

- [ ] **Step 5: README in beiden Fassungen ergänzen**

In `README.md` nach dem Abschnitt „Collection-Modus (Bookmarks statt Crawl)" einfügen:

```markdown
### Dokumente (PDFs statt URLs)

PDFs von der Platte werden seitenweise zu Markdown und landen wie gecrawlte
Seiten unter `raw/`:

```bash
illico-ingest documents ./ordner --label handbuecher
```

Seiten mit eingebetteter Textebene werden direkt übernommen und kosten nichts.
Nur Scans gehen an ein Vision-Modell (Default `anthropic/claude-sonnet-5` —
bewusst nicht das günstigere Projektmodell, weil Haiku niedrigere
Bildauflösungen verarbeitet und Scans dann schlechter liest).

Das `--label` wird zur `domain:` der erzeugten Seiten, sie liegen also unter
`raw/handbuecher/…` und lassen sich mit `--only-domains handbuecher`
gezielt kompilieren.

Weitere Optionen: `--jobs`, `--fresh`, `--max-pages`, `--text-threshold`
(ab wie vielen Zeichen eine Seite als Textseite gilt, Default 200) und
`--force-vision` (Weiche abschalten, falls der Bestand durchgehend gescannt
ist).

Ein erneuter Lauf über unveränderte Dateien kostet nichts: Illico merkt sich
in `_documents.json`, welche PDF-Bytes schon extrahiert wurden.
```

Die englische Fassung an derselben Stelle in `README.en.md`, inhaltlich gleich.

Außerdem in beiden Datenverzeichnis-Bäumen `_documents.json` ergänzen:

```
  _documents.json       ← welche PDFs schon extrahiert wurden
```

- [ ] **Step 6: Gesamtsuite und Paketprüfung**

```bash
source .venv-pub/bin/activate && pytest -q
```

Erwartet: alles grün.

- [ ] **Step 7: Commit**

```bash
git add illico_ingest.py tests/test_documents_e2e.py README.md README.en.md
git commit -m "feat(documents): CLI-Subcommand und Dokumentation

illico-ingest documents <pfad> --label <name>. Die Bilanz nennt Text- und
Vision-Seiten getrennt — die Vision-Zahl ist zugleich die Kostenzeile."
```

---

## Self-Review

**Spec-Abdeckung:**

| Spec-Abschnitt | Task |
|---|---|
| Abhängigkeiten (pypdfium2, Pillow) | 1 |
| CLI-Oberfläche, alle acht Optionen | 6 |
| Dateiauswahl, rekursiv `*.pdf`, Einzeldatei | 5 |
| Slug relativ zur Wurzel | 1 |
| Parallelität, pdfium einsträngig | 5 |
| 200 dpi, PNG | 2 |
| Frontmatter-Vertrag | 1 |
| Extraktions-Cache, `pages_done` | 4, 5 |
| Weiche, `--text-threshold`, `--force-vision` | 3 |
| Fehlerbehandlung (Tabelle) | 3, 5, 6 |
| Abschlussbilanz | 5, 6 |
| Tests (reine Funktionen, Weiche, Cache, Integration) | 1, 3, 4, 5, 6 |
| Bestehende Wächter greifen ohne Änderung | 1 (Step 6) |

**Offen aus der Spec, bewusst in Task 6 verortet:** die Prüfung, dass kein
Codepfad `source_url` tatsächlich abzurufen versucht. Dafür genügt beim
Umsetzen ein `grep -rn "source_url" *.py` und ein Blick, ob irgendwo ein
`httpx`-Aufruf daran hängt. Ergibt sich dort ein Treffer, ist das ein Befund
für eine eigene Runde — nicht still umbauen.

**Typkonsistenz geprüft:** `finish_page` liefert überall
`tuple[str, bool]`; der Treiber nutzt dieselbe Zerlegung. `prepare_page`
liefert überall `PreparedPage`, und genau eines von `markdown`/`png` ist
gesetzt. `file_hash` liefert
überall den `sha256:`-Präfix. `find_pdfs` liefert überall das Tripel
`(root, pdfs, skipped)`. `IngestReport`-Feldnamen sind zwischen Task 5 und 6
identisch.

**Keine Duplikation der Weichenlogik.** Ein früherer Entwurf ließ den Treiber
die Weiche nachbauen, weil Rendering im Hauptthread bleiben muss und der
LLM-Aufruf in den Pool gehört. Stattdessen ist die Weiche entlang genau dieser
Grenze geschnitten: `prepare_page` fasst nur das PDF an, `finish_page` nur das
Netz. Beide Stellen — Task 3s Tests und Task 5s Treiber — benutzen dieselben
zwei Funktionen. Wer den Treiber implementiert, darf die Weiche an keiner
Stelle erneut ausschreiben.
