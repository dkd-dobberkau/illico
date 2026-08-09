"""illico_documents — Dokumente (PDF) als Markdown nach raw/ bringen.

Text zuerst, Vision als Rueckfall: Seiten mit eingebetteter Textebene werden
direkt uebernommen und kosten nichts, Scans werden gerendert und an ein
Vision-Modell geschickt.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pypdfium2 as pdfium

import illico_llm
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


BLANK_PAGE_SENTINEL = "(leere Seite)"

VISION_PROMPT = f"""Gib den Inhalt dieser Dokumentseite als Markdown wieder.

- Nur der Seiteninhalt, keine Einleitung und kein Nachwort.
- Ueberschriften als Markdown-Ueberschriften, Tabellen als Markdown-Tabellen,
  Listen als Listen.
- Abbildungen und Diagramme in einem Satz beschreiben, in eckigen Klammern.
- Kopf- und Fusszeilen, Seitenzahlen und Wasserzeichen weglassen.
- Schreibe in der Sprache der Seite.
- Ist die Seite leer, antworte mit genau: {BLANK_PAGE_SENTINEL}
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

    Normalerweise ist genau eines von `markdown` und `png` gesetzt: entweder
    die Textebene hat gereicht, oder die Seite muss noch ans Modell. Sind
    beide None, braeuchte die Seite einen Vision-Aufruf, durfte ihn aber nicht
    bekommen (`allow_render=False`, also Budget erschoepft) — siehe
    `needs_vision`.
    """
    page_no: int
    markdown: str | None = None
    png: bytes | None = None

    @property
    def needs_vision(self) -> bool:
        """True, wenn die Seite einen bezahlten Aufruf braucht, aber keinen
        bekommen hat. Die Seite darf dann nicht als erledigt gelten."""
        return self.markdown is None and self.png is None


def prepare_page(
    page,
    page_no: int,
    threshold: int = DEFAULT_TEXT_THRESHOLD,
    force_vision: bool = False,
    dpi: int = DEFAULT_DPI,
    allow_render: bool = True,
) -> PreparedPage:
    """Der PDF-Teil: Textebene lesen oder rendern. Kein Netzaufruf.

    Laeuft im Hauptthread, weil PDFium nicht ohne Weiteres threadsicher ist.

    `allow_render=False` heisst: das Vision-Budget ist alle. Die Textebene
    wird trotzdem geprueft — sie kostet nichts und soll weiterlaufen —, aber
    fuer eine Seite, die Vision braeuchte, wird nicht mehr gerendert. Das
    Rendern ist der teuerste CPU-Teil des Laufs; es fuer Seiten zu erledigen,
    die anschliessend verworfen werden, waere bei grossen Bestaenden und
    kleinem Deckel der Loewenanteil der Laufzeit.
    """
    if not force_vision:
        text = extract_text(page)
        if is_text_sufficient(text, threshold):
            return PreparedPage(page_no=page_no, markdown=text.strip() + "\n")
    if not allow_render:
        return PreparedPage(page_no=page_no)
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
        call = illico_llm.call_sync

    markdown = vision_markdown(prepared.png, model, call)
    if not markdown or not markdown.strip():
        raise PageExtractionError("Modell lieferte eine leere Antwort")
    return markdown.strip() + "\n", True


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
    """Speichert das Manifest atomar (temp + os.replace).

    Ohne den atomaren Swap koennte ein Absturz mitten im Schreiben die Datei
    zerreissen; load_manifest wuerde den JSONDecodeError schlucken und den
    gesamten Fortschritt ALLER Labels verwerfen, nicht nur den gerade
    geschriebenen. Gleiches Muster wie illico_inventory.save_inventory,
    illico_distill.DistillStore.put und illico_crawl_status.save_crawl_status.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    os.replace(tmp, path)


def pending_pages(entry: dict | None, pages_total: int) -> list[int]:
    """Noch fehlende Seiten, 1-basiert.

    Weicht die gespeicherte Seitenzahl ab, ist der Eintrag unbrauchbar und
    alles wird neu geholt.
    """
    if not entry or entry.get("pages_total") != pages_total:
        return list(range(1, pages_total + 1))
    done = set(entry.get("pages_done", []))
    return [n for n in range(1, pages_total + 1) if n not in done]


@dataclass
class IngestReport:
    documents: int = 0
    documents_skipped: int = 0
    non_pdf_skipped: int = 0
    pages_text: int = 0
    pages_vision: int = 0
    pages_blank: int = 0
    pages_failed: int = 0
    # Die Kostenzeile: jeder versuchte Modellaufruf, auch die, die als leere
    # Seite zurueckkommen oder scheitern. pages_vision zaehlt nur die Aufrufe
    # mit Ausbeute und ist als Rechnungsgrundlage zu niedrig.
    vision_calls: int = 0
    capped: bool = False
    errors: list[str] = field(default_factory=list)


def validate_label(label: str) -> None:
    """Weist Labels ab, die als Pfad-/Praefix-Bestandteil ausbrechen koennten.

    `data / "raw" / label` und der Manifest-Schluessel `f"{label}/..."` nehmen
    das Label ungeprueft entgegen. Ein `--label ../../x` schriebe ausserhalb
    des Datenverzeichnisses; ein `/` darin macht auch den `--fresh`-Praefix-
    Filter (`f"{label}/"`) zweideutig. slugify() (illico_inventory.py) loest
    dasselbe Problem fuer LLM-generierte Slugs; hier reicht eine klare
    Ablehnung, weil das Label vom Aufrufer stammt und lesbar bleiben soll.
    """
    if not label or "/" in label or "\\" in label or ".." in label:
        raise ValueError(
            f"Ungueltiges Label {label!r}: darf weder '/' noch '\\' noch "
            "'..' enthalten."
        )


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


def _ingest_one_document(
    pdf_path: Path,
    root: Path,
    label: str,
    out_dir: Path,
    manifest: dict,
    manifest_path: Path,
    model: str,
    jobs: int,
    budget: int | None,
    threshold: int,
    force_vision: bool,
    dpi: int,
    call,
    report: IngestReport,
) -> int | None:
    """Verarbeitet ein PDF und schreibt seine fehlenden Seiten nach `out_dir`.

    Seiten laufen in Chunks von `max(1, jobs) * 4`, statt alle PNGs des
    Dokuments vorab im Speicher zu halten — bei 300 Scan-Seiten waeren das
    sonst 150-600 MB gleichzeitig resident. Nach jedem Chunk wird das Manifest
    gesichert (try/finally um den Pool), damit ein Abbruch (Ctrl-C,
    LLMAuthError, Crash) nur den laufenden Chunk kostet statt das ganze
    Dokument — pages_done haelt exakt das, was schon auf der Platte liegt.

    Gibt das verbleibende Budget zurueck (None, wenn --max-pages nicht
    gesetzt ist).
    """
    from illico_ingest import detect_language

    rel_source = str(pdf_path.relative_to(root))
    manifest_key = f"{label}/{rel_source}"
    try:
        digest = file_hash(pdf_path)
        pdf = open_document(pdf_path)
        pages_total = len(pdf)
    except Exception as exc:
        report.errors.append(f"{rel_source}: {exc}")
        return budget

    # Schluessel ist Label+Pfad, der Hash ist der Aenderungsdetektor.
    # Ohne das Label im Schluessel teilten sich zwei Ingests mit
    # gleicher relativer Struktur einen Eintrag, und das zweite
    # Dokument bekaeme keine raw/-Dateien — derselbe stille Verlust wie
    # beim reinen Hash-Schluessel, eine Ebene hoeher.
    entry = manifest.get(manifest_key)
    if entry is not None and entry.get("hash") != digest:
        entry = None
    todo = pending_pages(entry, pages_total)
    if not todo:
        report.documents_skipped += 1
        return budget

    slug = document_slug(pdf_path, root)
    title = document_title(pdf, fallback=pdf_path.stem)
    done = set((entry or {}).get("pages_done", []))

    chunk_size = max(1, jobs) * 4
    for start in range(0, len(todo), chunk_size):
        chunk = todo[start:start + chunk_size]

        # Sequenziell im Hauptthread: PDF anfassen. PDFium ist nicht ohne
        # Weiteres threadsicher, und der Teil ist CPU-gebunden und schnell.
        prepared: list[PreparedPage] = []
        for page_no in chunk:
            try:
                page = prepare_page(
                    pdf[page_no - 1], page_no=page_no, threshold=threshold,
                    force_vision=force_vision, dpi=dpi,
                    allow_render=budget is None or budget > 0,
                )
            except Exception as exc:
                report.errors.append(f"{rel_source} Seite {page_no}: {exc}")
                report.pages_failed += 1
                continue

            if page.needs_vision:
                # Budget alle. Die Seite bleibt offen und wird beim naechsten
                # Lauf nachgeholt; Textseiten weiter hinten laufen trotzdem
                # durch, weil sie nichts kosten.
                report.capped = True
                continue

            if page.png is not None:
                # Beim VERSUCH abziehen, nicht erst beim Erfolg — sonst bewegt
                # sich budget nie, wenn jede Seite scheitert (kaputtes Modell,
                # 400er auf uebergrossen Bildern), und das naechste Dokument
                # bekommt wieder das volle Budget. Bis zu documents *
                # max_pages abrechenbare Aufrufe waeren die Folge.
                report.vision_calls += 1
                if budget is not None:
                    budget -= 1

            prepared.append(page)

        try:
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

                    if via_vision and markdown.strip() == BLANK_PAGE_SENTINEL:
                        # Leere Seite: kein raw/-File, kein Inhalt zaehlen —
                        # aber als erledigt vermerken, sonst wird sie bei
                        # jedem Lauf erneut (kostenpflichtig) angefragt.
                        done.add(page_no)
                        report.pages_blank += 1
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
        finally:
            manifest[manifest_key] = {
                "hash": digest,
                "pages_total": pages_total, "pages_done": sorted(done),
            }
            save_manifest(manifest_path, manifest)

    report.documents += 1
    return budget


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
    validate_label(label)
    if call is None:
        call = illico_llm.call_sync

    root, pdfs, non_pdf = find_pdfs(target)
    report = IngestReport(non_pdf_skipped=non_pdf)

    out_dir = data / "raw" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = data / MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    if fresh:
        # _documents.json ist eine Datei fuer alle Labels. Ein kompletter
        # Reset wuerde fremde Labels in eine volle Neuextraktion zwingen —
        # nur die Eintraege dieses Labels verwerfen.
        prefix = f"{label}/"
        manifest = {key: value for key, value in manifest.items()
                    if not key.startswith(prefix)}
    budget = max_pages

    for pdf_path in pdfs:
        if budget is not None and budget <= 0:
            # Budget schon vor diesem Dokument aufgebraucht: gar nicht erst
            # hashen/oeffnen, und deutlich machen, dass der Lauf wegen der
            # Obergrenze endet statt weil der Bestand zu Ende ist.
            report.capped = True
            break
        budget = _ingest_one_document(
            pdf_path=pdf_path, root=root, label=label, out_dir=out_dir,
            manifest=manifest, manifest_path=manifest_path, model=model,
            jobs=jobs, budget=budget, threshold=threshold,
            force_vision=force_vision, dpi=dpi, call=call, report=report,
        )

    return report
