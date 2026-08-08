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
