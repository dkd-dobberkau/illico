"""Generische Frontmatter-Parser für Illico — Kern-Modul (offen).

Kennt kein Tenant-Konzept. Wird von illico_app und optionalen Overlays genutzt.
"""

import re
from urllib.parse import urlparse

_SOURCES_RE = re.compile(r"^sources:\s*\[(.*?)\]\s*$", re.MULTILINE)
_SOURCES_MULTILINE_RE = re.compile(
    r"^sources:[ \t]*\n((?:[ \t]+-[ \t]*.*(?:\n|$))+)", re.MULTILINE
)


def extract_sources(article_content: str) -> list[str]:
    """Parst die sources-Zeile aus dem Frontmatter.

    Akzeptiert zwei Frontmatter-Formate:
      1. Klassisch:   ``---`` Anfang, ``---`` Ende.
      2. LLM-Artefakt: `````yaml`` Anfang, ``---`` Ende
         (vereinzelt ``````` Ende). Tritt bei ~19 % der mit
         illico_compile erzeugten Artikel auf.

    Akzeptiert zwei sources-Notationen:
      a. Inline:   ``sources: ["a.md", "b.md"]``
      b. YAML-Liste über mehrere Zeilen:
         ```
         sources:
           - "a.md"
           - "b.md"
         ```
    """
    text = article_content.lstrip()

    if text.startswith("---"):
        body_start = text.find("\n", 3)
        if body_start == -1:
            return []
        end = text.find("---", body_start)
    elif text.startswith("```yaml"):
        body_start = text.find("\n", 7)
        if body_start == -1:
            return []
        body_start += 1  # nach dem Newline beginnt der Frontmatter-Body
        # Manche LLM-Artefakte schreiben direkt nach ```yaml noch ein '---'
        # als zweiten Opener — die überspringen wir.
        stripped_head = text[body_start:].lstrip("\n")
        if stripped_head.startswith("---"):
            skipped = len(text[body_start:]) - len(stripped_head)
            body_start = body_start + skipped + 3
        # Übliche LLM-Konvention: Schluss-Marker ist '---'.
        # Fallback: schließendes ``` (falls der LLM doch konsistent fenced).
        end = text.find("---", body_start)
        if end == -1:
            end = text.find("```", body_start)
    else:
        return []

    if end == -1:
        return []

    frontmatter = text[body_start:end]

    # Variante a: Inline-Form sources: [...].
    match = _SOURCES_RE.search(frontmatter)
    if match:
        inner = match.group(1)
        return [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]

    # Variante b: YAML-Listenform über mehrere Zeilen.
    multi = _SOURCES_MULTILINE_RE.search(frontmatter)
    if multi:
        out: list[str] = []
        for line in multi.group(1).splitlines():
            stripped = line.strip()
            if not stripped.startswith("-"):
                continue
            val = stripped[1:].strip().strip('"').strip("'")
            if val:
                out.append(val)
        return out

    return []


def extract_raw_domain(content: str) -> str | None:
    """Liest `domain:` aus dem YAML-Frontmatter; fällt auf source_url/url zurück.

    Erwartet das klassische `---`-Format (Raw-Files sind immer so). Für die
    LLM-Artefakte (```yaml-Fence) ist das nicht gebraucht — die kommen
    aus Wiki-Artikeln, nicht aus Raw.
    """
    text = content.lstrip()
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    domain: str | None = None
    url_val: str | None = None
    for line in text[3:end].split("\n"):
        stripped = line.strip()
        if stripped.startswith("domain:"):
            domain = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            break
        if url_val is None and (
            stripped.startswith("source_url:") or stripped.startswith("url:")
        ):
            url_val = stripped.split(":", 1)[1].strip().strip('"').strip("'")
    if domain:
        return domain
    if url_val:
        parsed = urlparse(url_val)
        if parsed.netloc:
            return parsed.netloc
    return None
