"""illico_documents — Dokumente (PDF) als Markdown nach raw/ bringen.

Text zuerst, Vision als Rueckfall: Seiten mit eingebetteter Textebene werden
direkt uebernommen und kosten nichts, Scans werden gerendert und an ein
Vision-Modell geschickt.
"""
from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pypdfium2 as pdfium

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
