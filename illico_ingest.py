"""
illico ingest — crawlt eine Website und legt saubere Markdown-Dateien in raw/ ab.

Usage:
    python ingest.py https://example.com
    python ingest.py https://example.com --depth 2 --data ./my-wiki
"""

import os
import re
import json
import gzip
import time
import hashlib
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from typing import Optional
from xml.etree import ElementTree as ET

import httpx
import typer
import trafilatura
from bs4 import BeautifulSoup
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich import print as rprint
from langdetect import detect as _langdetect, DetectorFactory, LangDetectException

from illico_crawl_status import classify_block, load_crawl_status, save_crawl_status

# Deterministische Spracherkennung (langdetect ist standardmaessig nicht-deterministisch)
DetectorFactory.seed = 0

app = typer.Typer()
console = Console()

HEADERS = {
    "User-Agent": "Illico/0.1 (knowledge-base-builder; +https://github.com/illico)"
}

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# 429-Handling: begrenzte Retries pro URL, plus globaler Abbruch bei anhaltender
# Drosselung. Ein Host, der die Crawl-IP per WAF blockt, lieferte sonst pro URL
# MAX_429_RETRIES x MAX_RETRY_WAIT Sekunden Leerlauf und der Crawl produzierte
# ueber Stunden nichts (siehe typo3-solr.com auf der Prod-IP).
MAX_429_RETRIES = 3          # Versuche pro URL, bevor sie als terminaler 429 gilt
MAX_RETRY_WAIT = 30          # Sekunden-Deckel je Retry-Wartezeit (Retry-After gekappt)
CONSECUTIVE_429_ABORT = 5    # so viele URLs in Folge mit terminalem 429 -> Crawl-Abbruch


def url_to_filename(url: str, base_url: str) -> Path:
    """Konvertiert eine URL in einen relativen Dateipfad."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")

    if not path:
        return Path("index.md")

    # Entferne .html, .htm Endungen
    path = re.sub(r"\.(html?|php|aspx?)$", "", path)

    # Ersetze Slashes durch Verzeichnisstruktur
    parts = [p for p in path.split("/") if p]
    if parts:
        return Path(*parts).with_suffix(".md")
    return Path("index.md")


def extract_links(html: str, base_url: str, allowed_domain: str) -> list[str]:
    """Extrahiert alle internen Links aus einer HTML-Seite."""
    soup = BeautifulSoup(html, "lxml")
    links = []

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()

        # Ignoriere Anker, mailto, javascript
        if href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue

        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        # Nur gleiche Domain, kein Fragment
        if parsed.netloc == allowed_domain:
            clean = full_url.split("#")[0].rstrip("/")
            if clean not in links:
                links.append(clean)

    return links


def parse_bookmarks_html(html: str) -> list[str]:
    """Extrahiert http(s)-URLs aus einem Netscape-Bookmarks-Export.

    Reihenfolgetreu dedupliziert; Nicht-Web-Schemata (javascript:, place:,
    chrome://, data:, …) werden verworfen. Ordnerstruktur wird flach ignoriert.
    """
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href.lower().startswith(("http://", "https://")):
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
    return urls


def find_markdown_alternate(html: str, base_url: str) -> Optional[str]:
    """Sucht nach <link rel="alternate" type="text/markdown"> im HTML."""
    soup = BeautifulSoup(html, "lxml")
    link = soup.find("link", rel="alternate", type="text/markdown")
    if link and link.get("href"):
        return urljoin(base_url, link["href"])
    return None


def parse_robots_for_sitemaps(client: httpx.Client, base_url: str) -> list[str]:
    """Liest robots.txt und extrahiert alle Sitemap:-Eintraege."""
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = client.get(robots_url)
    except Exception:
        return []
    if resp.status_code != 200:
        return []
    sitemaps: list[str] = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line.lower().startswith("sitemap:"):
            sm = line.split(":", 1)[1].strip()
            if sm:
                sitemaps.append(sm)
    return sitemaps


def load_robots_parser(client: httpx.Client, base_url: str) -> Optional[RobotFileParser]:
    """Laedt robots.txt einmal pro Host und gibt einen geparsten RobotFileParser zurueck.

    Rueckgabe None heisst: keine robots.txt erreichbar -> alles erlaubt (RFC-konform).
    HTTP 5xx behandeln wir konservativ als "alles erlaubt" (sonst koennten flaky Hosts
    den Crawl komplett blockieren).
    """
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = client.get(robots_url)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    rp = RobotFileParser()
    # Workaround fuer CPython-Verhalten: Leerzeilen beenden den aktiven
    # User-agent-Block, wodurch ein `User-agent: *\n\nDisallow: ...`-Layout
    # (wie es manche robots.txt nutzen) komplett verworfen wird. Vorher rausfiltern.
    lines = [l for l in resp.text.splitlines() if l.strip()]
    rp.parse(lines)
    return rp


def fetch_sitemap_content(client: httpx.Client, sitemap_url: str) -> Optional[bytes]:
    """Holt eine Sitemap und entpackt sie bei Bedarf (gzip)."""
    try:
        resp = client.get(sitemap_url)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    content = resp.content
    # gzip-Magic-Bytes pruefen (auch wenn URL nicht .gz endet)
    if sitemap_url.endswith(".gz") or content[:2] == b"\x1f\x8b":
        try:
            content = gzip.decompress(content)
        except OSError:
            return None
    return content


def parse_sitemap(
    client: httpx.Client,
    sitemap_url: str,
    allowed_domain: str,
    max_urls: int,
    seen_sitemaps: Optional[set] = None,
    depth: int = 0,
) -> list[str]:
    """Parst eine sitemap.xml oder einen Sitemap-Index (rekursiv, mit Loop-Schutz)."""
    if seen_sitemaps is None:
        seen_sitemaps = set()
    if sitemap_url in seen_sitemaps or depth > 3 or max_urls <= 0:
        return []
    seen_sitemaps.add(sitemap_url)

    content = fetch_sitemap_content(client, sitemap_url)
    if not content:
        return []

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    urls: list[str] = []

    if tag == "sitemapindex":
        for sm in root.findall(f"{SITEMAP_NS}sitemap"):
            loc = sm.find(f"{SITEMAP_NS}loc")
            if loc is None or not loc.text:
                continue
            nested = parse_sitemap(
                client, loc.text.strip(), allowed_domain,
                max_urls - len(urls), seen_sitemaps, depth + 1,
            )
            urls.extend(nested)
            if len(urls) >= max_urls:
                return urls[:max_urls]
    elif tag == "urlset":
        for u in root.findall(f"{SITEMAP_NS}url"):
            loc = u.find(f"{SITEMAP_NS}loc")
            if loc is None or not loc.text:
                continue
            url = loc.text.strip()
            parsed = urlparse(url)
            if parsed.netloc == allowed_domain:
                clean = url.split("#")[0].rstrip("/")
                urls.append(clean)
                if len(urls) >= max_urls:
                    return urls

    return urls


def discover_sitemap_urls(
    client: httpx.Client,
    start_url: str,
    allowed_domain: str,
    explicit_url: Optional[str],
    max_urls: int,
) -> list[str]:
    """Findet Seed-URLs aus Sitemaps — explizit, via robots.txt oder /sitemap.xml."""
    candidates: list[str] = []
    if explicit_url:
        candidates.append(explicit_url)
    else:
        candidates.extend(parse_robots_for_sitemaps(client, start_url))
        if not candidates:
            parsed = urlparse(start_url)
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/sitemap.xml")

    all_urls: list[str] = []
    seen_sitemaps: set = set()
    for sm in candidates:
        if len(all_urls) >= max_urls:
            break
        all_urls.extend(
            parse_sitemap(client, sm, allowed_domain, max_urls - len(all_urls), seen_sitemaps)
        )

    # Dedupe, Reihenfolge erhalten
    out: list[str] = []
    seen: set = set()
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:max_urls]


def html_to_markdown(html: str, url: str) -> Optional[str]:
    """Konvertiert HTML zu sauberem Markdown via trafilatura."""
    result = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_images=False,
        include_tables=True,
        no_fallback=False,
    )
    return result


def detect_language(html: Optional[str], markdown: str) -> Optional[str]:
    """Zweistufige Spracherkennung: erst <html lang>, dann langdetect auf Inhalt."""
    # Stufe 1: html lang attribute (billig, in den meisten Sites korrekt)
    if html:
        try:
            soup = BeautifulSoup(html, "lxml")
            tag = soup.find("html")
            if tag and tag.get("lang"):
                lang = str(tag["lang"]).strip().lower()
                if lang:
                    return lang.split("-")[0]  # "de-DE" -> "de"
        except Exception:
            pass

    # Stufe 2: Content-Detection via langdetect
    # Frontmatter abschneiden, falls vorhanden
    text = markdown
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    text = text.strip()[:2000]
    if len(text) < 50:
        return None
    try:
        return _langdetect(text)
    except LangDetectException:
        return None


def extract_title(html: str, url: str) -> str:
    """Extrahiert den Seitentitel."""
    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text().strip()
    return urlparse(url).path.strip("/") or "index"


def build_frontmatter(title: str, url: str, domain: str, language: Optional[str] = None) -> str:
    """Erstellt YAML Frontmatter für die Markdown-Datei."""
    from datetime import datetime
    date = datetime.now().strftime("%Y-%m-%d")
    lang_line = f'language: "{language}"\n' if language else ""
    return f"""---
title: "{title.replace('"', "'")}"
source_url: "{url}"
domain: "{domain}"
crawled: "{date}"
{lang_line}---

"""


def load_history(output_dir: Path) -> dict:
    """Lädt die Crawl-History aus _crawl-history.json."""
    path = output_dir / "_crawl-history.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"urls": {}}


def save_history(output_dir: Path, history: dict):
    """Speichert die Crawl-History."""
    path = output_dir / "_crawl-history.json"
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def crawl(
    start_url: str,
    output_dir: Path,
    max_depth: int = 2,
    delay: float = 0.5,
    fresh: bool = False,
    use_sitemap: bool = True,
    sitemap_url: Optional[str] = None,
    sitemap_max: int = 5000,
    target_langs: Optional[list[str]] = None,
    max_pages: Optional[int] = None,
    max_consecutive_429: int = CONSECUTIVE_429_ABORT,
) -> dict:
    """Hauptcrawler — traversiert die Site und speichert Markdown-Dateien."""

    parsed_start = urlparse(start_url)
    allowed_domain = parsed_start.netloc
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    history = load_history(output_dir) if not fresh else {"urls": {}}
    previously_crawled = set(history["urls"].keys())

    visited = set()
    queue = [(start_url, 0)]  # (url, depth)
    results = {"success": [], "failed": [], "skipped": [], "cached": []}
    current_delay = delay
    consecutive_429 = 0  # aufeinanderfolgende URLs mit terminalem 429 (Drossel-Abbruch)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total} Seiten"),
        console=console,
    ) as progress:
        task = progress.add_task("Crawling...", total=None)

        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            progress.update(task, description="[cyan]robots.txt laden...")
            robots = load_robots_parser(client, start_url)
            if robots is None:
                console.print("[dim]robots.txt: nicht vorhanden / nicht lesbar — alles erlaubt[/dim]")
            else:
                console.print("[cyan]robots.txt: respektiert[/cyan]")

            if use_sitemap or sitemap_url:
                progress.update(task, description="[cyan]Sitemap suchen...")
                seed_urls = discover_sitemap_urls(
                    client, start_url, allowed_domain, sitemap_url, sitemap_max,
                )
                if seed_urls:
                    console.print(f"[cyan]Sitemap: {len(seed_urls)} URLs gefunden, in Queue aufgenommen[/cyan]")
                    queued = {start_url}
                    for u in seed_urls:
                        if u not in queued:
                            queue.append((u, 0))
                            queued.add(u)
                elif sitemap_url:
                    console.print(f"[yellow]Sitemap {sitemap_url} nicht erreichbar/leer[/yellow]")

            while queue:
                url, depth = queue.pop(0)

                if url in visited:
                    results["skipped"].append(url)
                    continue

                visited.add(url)

                if url in previously_crawled:
                    results["cached"].append(url)
                    continue

                if robots is not None and not robots.can_fetch(HEADERS["User-Agent"], url):
                    results["skipped"].append(url)
                    continue

                progress.update(task, description=f"[blue]{url[:60]}...")

                try:
                    # Request mit begrenztem Retry bei 429
                    response = None
                    for attempt in range(MAX_429_RETRIES):
                        response = client.get(url)
                        if response.status_code != 429:
                            break
                        # Nach dem letzten Versuch nicht mehr warten — es folgt kein
                        # Retry, die URL wird gleich als terminaler 429 verbucht.
                        if attempt == MAX_429_RETRIES - 1:
                            break
                        retry_after = int(response.headers.get("retry-after", 2 ** attempt))
                        wait = min(retry_after, MAX_RETRY_WAIT)
                        progress.update(task, description=f"[yellow]429 — warte {wait}s...")
                        time.sleep(wait)
                        current_delay = min(current_delay * 2, 5.0)  # adaptiv verlangsamen

                    # Persistente Drosselung erkennen: bleibt der Host trotz Retries bei
                    # 429, den GANZEN Crawl abbrechen, statt jede weitere URL erneut mit
                    # Wartezeiten zu verbrennen (ein blockender Host lieferte sonst ueber
                    # Stunden nichts). Nur aufeinanderfolgende 429 zaehlen.
                    if response.status_code == 429:
                        consecutive_429 += 1
                        results["failed"].append((url, "HTTP 429 (Rate-Limit)"))
                        if consecutive_429 >= max_consecutive_429:
                            console.print(
                                f"[red]Host drosselt dauerhaft (429) — Crawl nach "
                                f"{consecutive_429} aufeinanderfolgenden Blocks abgebrochen. "
                                f"Andere IP/User-Agent noetig oder spaeter erneut versuchen.[/red]"
                            )
                            break
                        continue

                    # Jede Nicht-429-Antwort setzt den Block-Zaehler zurueck
                    consecutive_429 = 0

                    if response.status_code != 200:
                        results["failed"].append((url, f"HTTP {response.status_code}"))
                        continue

                    content_type = response.headers.get("content-type", "")
                    if "text/html" not in content_type:
                        results["skipped"].append(url)
                        continue

                    html = response.text

                    # Markdown-Alternative bevorzugen (z.B. b13.com)
                    md_alt_url = find_markdown_alternate(html, url)
                    md_alt_had_frontmatter = False
                    if md_alt_url:
                        md_resp = client.get(md_alt_url)
                        if md_resp.status_code == 200 and len(md_resp.text.strip()) >= 50:
                            markdown = md_resp.text
                            md_alt_had_frontmatter = markdown.lstrip().startswith("---")
                            progress.update(task, description=f"[green]MD: {url[:55]}...")
                        else:
                            md_alt_url = None

                    if not md_alt_url:
                        # Fallback: HTML zu Markdown via trafilatura
                        markdown = html_to_markdown(html, url)
                        if not markdown or len(markdown.strip()) < 50:
                            results["skipped"].append(url)
                            continue

                    # Sprache erkennen (einmal pro Seite) — fuer Frontmatter UND Filter
                    detected_lang = detect_language(html, markdown)

                    # Sprachfilter: wenn target_langs gesetzt, Seiten anderer Sprachen ueberspringen.
                    # Unbekannte Sprache (None) wird NICHT gefiltert (konservativ).
                    if target_langs and detected_lang and detected_lang not in target_langs:
                        results["skipped"].append(url)
                        progress.update(task, description=f"[yellow]Sprache {detected_lang}: {url[:50]}...")
                        continue

                    # Frontmatter setzen (wenn nicht schon im md_alt-Stream vorhanden)
                    if not md_alt_had_frontmatter:
                        title = extract_title(html, url)
                        markdown = build_frontmatter(title, url, allowed_domain, detected_lang) + markdown

                    # Dateiname bestimmen
                    rel_path = url_to_filename(url, start_url)
                    file_path = raw_dir / rel_path
                    file_path.parent.mkdir(parents=True, exist_ok=True)

                    # Kollisionen vermeiden
                    if file_path.exists():
                        stem = file_path.stem
                        suffix = hashlib.md5(url.encode()).hexdigest()[:6]
                        file_path = file_path.with_stem(f"{stem}_{suffix}")

                    # Datei schreiben
                    file_path.write_text(markdown, encoding="utf-8")
                    results["success"].append((url, str(rel_path)))
                    history["urls"][url] = {
                        "file": str(rel_path),
                        "crawled": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                    progress.update(task, advance=1, total=len(visited) + len(queue))

                    if max_pages is not None and len(results["success"]) >= max_pages:
                        console.print(f"[yellow]Limit erreicht: {max_pages} Seiten gespeichert, Crawl beendet[/yellow]")
                        break

                    # Links weiterverfolgen (wenn depth erlaubt)
                    if depth < max_depth:
                        links = extract_links(html, url, allowed_domain)
                        for link in links:
                            if link not in visited:
                                queue.append((link, depth + 1))

                    time.sleep(current_delay)

                except Exception as e:
                    results["failed"].append((url, str(e)))

    save_history(output_dir, history)

    # Block-Status je Domain persistieren (selbstheilend: ein erfolgreicher
    # Re-Crawl überschreibt einen alten blocked=true-Eintrag). Der Status ist
    # Beiwerk — ein Schreibfehler (read-only FS) darf das Crawl-Ergebnis nicht
    # gefährden.
    # Kehrseite von "letzter Crawl gewinnt": ein Re-Crawl einer eigentlich
    # gesunden Site, die gleich zu Beginn einen transienten 429-Spike hat (früher
    # Abbruch → ok=0), überschreibt den guten Eintrag kurzzeitig mit blocked=true.
    # Das Dashboard zeigt dann fälschlich "Blockiert"; nur ein Label, blockt nichts,
    # und der nächste erfolgreiche Crawl korrigiert es selbst.
    try:
        status = load_crawl_status(output_dir)
        block = classify_block(results)
        status["domains"][allowed_domain] = {
            "last_crawl": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "ok": block["ok"],
            "failed": block["failed"],
            "blocked": block["blocked"],
            "reason": block["reason"],
            "top_status": block["top_status"],
        }
        save_crawl_status(output_dir, status)
    except OSError:
        pass

    return results


def collect(
    urls: list[str],
    output_dir: Path,
    delay: float = 0.5,
    fresh: bool = False,
    target_langs: Optional[list[str]] = None,
    max_pages: Optional[int] = None,
) -> dict:
    """Holt eine kuratierte URL-Liste (je URL einmal, kein BFS) und speichert
    Markdown domain-präfixiert in raw/<domain>/…. Reine Liste — keine Sitemap,
    keine Adaptiv-Drossel, kein Block-Status."""
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    history = load_history(output_dir) if not fresh else {"urls": {}}
    previously = set(history["urls"].keys())
    results = {"success": [], "failed": [], "skipped": [], "cached": []}

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        for url in urls:
            if url in previously:
                results["cached"].append(url)
                continue
            try:
                response = client.get(url)
                if response.status_code != 200:
                    results["failed"].append((url, f"HTTP {response.status_code}"))
                    continue
                if "text/html" not in response.headers.get("content-type", ""):
                    results["skipped"].append(url)
                    continue

                html = response.text
                markdown = html_to_markdown(html, url)
                if not markdown or len(markdown.strip()) < 50:
                    results["skipped"].append(url)
                    continue

                detected_lang = detect_language(html, markdown)
                if target_langs and detected_lang and detected_lang not in target_langs:
                    results["skipped"].append(url)
                    continue

                domain = urlparse(url).netloc
                markdown = build_frontmatter(extract_title(html, url), url, domain, detected_lang) + markdown

                file_path = raw_dir / domain / url_to_filename(url, url)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                if file_path.exists():
                    stem = file_path.stem
                    suffix = hashlib.md5(url.encode()).hexdigest()[:6]
                    file_path = file_path.with_stem(f"{stem}_{suffix}")

                file_path.write_text(markdown, encoding="utf-8")
                rel_path = file_path.relative_to(raw_dir)
                results["success"].append((url, str(rel_path)))
                history["urls"][url] = {
                    "file": str(rel_path),
                    "crawled": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }

                if max_pages is not None and len(results["success"]) >= max_pages:
                    break
                time.sleep(delay)
            except Exception as e:
                results["failed"].append((url, str(e)))

    save_history(output_dir, history)
    return results


def print_summary(results: dict, output_dir: Path):
    """Gibt eine Zusammenfassung des Crawls aus."""
    console.print()

    table = Table(title="[bold]Illico Ingest — Zusammenfassung[/bold]", show_header=True)
    table.add_column("Status", style="bold")
    table.add_column("Anzahl", justify="right")

    table.add_row("[green]✓ Erfolgreich[/green]", str(len(results["success"])))
    table.add_row("[cyan]⟳ Bereits gecrawlt[/cyan]", str(len(results["cached"])))
    table.add_row("[red]✗ Fehlgeschlagen[/red]", str(len(results["failed"])))
    table.add_row("[yellow]⊘ Übersprungen[/yellow]", str(len(results["skipped"])))

    console.print(table)
    console.print()

    if results["success"]:
        console.print(f"[green]Markdown-Dateien gespeichert in:[/green] {output_dir / 'raw'}")
        console.print()
        console.print("[bold]Gespeicherte Dateien:[/bold]")
        for url, path in results["success"]:
            console.print(f"  [dim]{path}[/dim]  ←  {url[:70]}")

    if results["failed"]:
        console.print()
        console.print("[bold red]Fehler:[/bold red]")
        for url, error in results["failed"]:
            console.print(f"  [red]{url[:60]}[/red] — {error}")

    console.print()
    console.print("[bold blue]Nächster Schritt:[/bold blue] [cyan]python compile.py[/cyan]")


@app.command()
def ingest(
    url: str = typer.Argument(..., help="Start-URL der Website"),
    depth: int = typer.Option(2, "--depth", help="Crawl-Tiefe (Standard: 2)"),
    data: Path = typer.Option(Path(os.environ.get("ILLICO_DATA", "./illico-data")), "--data", "-d", help="Illico-Datenverzeichnis"),
    delay: float = typer.Option(0.5, "--delay", help="Pause zwischen Requests in Sekunden"),
    fresh: bool = typer.Option(False, "--fresh", help="Ignoriere Crawl-History, alles neu crawlen"),
    sitemap: bool = typer.Option(True, "--sitemap/--no-sitemap", help="Sitemap-Autoerkennung via robots.txt + /sitemap.xml"),
    sitemap_url: Optional[str] = typer.Option(None, "--sitemap-url", help="Explizite Sitemap-URL (uebersteuert Auto-Erkennung)"),
    sitemap_max: int = typer.Option(5000, "--sitemap-max", help="Maximale URLs, die aus Sitemap uebernommen werden"),
    lang: Optional[str] = typer.Option(None, "--lang", help="Nur Seiten dieser Sprache(n) behalten, ISO 639-1 kommagetrennt (z.B. 'de' oder 'de,en')"),
    max_pages: Optional[int] = typer.Option(None, "--max-pages", help="Harte Obergrenze fuer neu gespeicherte Seiten (Crawl bricht ab, sobald erreicht)"),
):
    """
    Crawlt eine Website und konvertiert alle Seiten zu Markdown-Dateien in raw/.
    """
    console.print()
    console.rule("[bold blue]ILLICO INGEST[/bold blue]")
    console.print(f"  URL:    [cyan]{url}[/cyan]")
    console.print(f"  Tiefe:  [cyan]{depth}[/cyan]")
    console.print(f"  Data:   [cyan]{data}[/cyan]")
    if sitemap_url:
        console.print(f"  Sitemap: [cyan]{sitemap_url}[/cyan]")
    elif sitemap:
        console.print(f"  Sitemap: [cyan]auto (robots.txt + /sitemap.xml)[/cyan]")
    else:
        console.print(f"  Sitemap: [dim]aus[/dim]")
    if max_pages is not None:
        console.print(f"  Limit:  [cyan]{max_pages} Seiten[/cyan]")

    target_langs: Optional[list[str]] = None
    if lang:
        target_langs = [l.strip().lower() for l in lang.split(",") if l.strip()]
        console.print(f"  Sprache: [cyan]{', '.join(target_langs)}[/cyan]")

    if not fresh:
        history = load_history(data)
        if history["urls"]:
            console.print(f"  Cache:  [cyan]{len(history['urls'])} URLs bekannt[/cyan]")
    console.print()

    results = crawl(
        url, data,
        max_depth=depth, delay=delay, fresh=fresh,
        use_sitemap=sitemap, sitemap_url=sitemap_url, sitemap_max=sitemap_max,
        target_langs=target_langs, max_pages=max_pages,
    )
    print_summary(results, data)


@app.command()
def collection(
    source: Path = typer.Argument(..., help="Bookmarks-HTML-Export (Netscape-Format)"),
    data: Path = typer.Option(Path(os.environ.get("ILLICO_DATA", "./illico-data")), "--data", "-d", help="Illico-Datenverzeichnis"),
    delay: float = typer.Option(0.5, "--delay", help="Pause zwischen Requests in Sekunden"),
    fresh: bool = typer.Option(False, "--fresh", help="Ignoriere Cache, alles neu holen"),
    lang: Optional[str] = typer.Option(None, "--lang", help="Nur Seiten dieser Sprache(n) behalten, ISO 639-1 kommagetrennt"),
    max_pages: Optional[int] = typer.Option(None, "--max-pages", help="Harte Obergrenze fuer neu gespeicherte Seiten"),
):
    """
    Holt eine kuratierte URL-Liste aus einem Browser-Bookmarks-Export und
    speichert jede Seite als Markdown in raw/ (kein Crawl, je URL einmal).
    """
    if not source.is_file():
        console.print(f"[red]Bookmarks-Datei nicht gefunden: {source}[/red]")
        raise typer.Exit(code=1)

    urls = parse_bookmarks_html(source.read_text(encoding="utf-8", errors="ignore"))

    console.print()
    console.rule("[bold blue]ILLICO COLLECTION[/bold blue]")
    console.print(f"  Quelle: [cyan]{source}[/cyan]")
    console.print(f"  URLs:   [cyan]{len(urls)} aus Bookmarks[/cyan]")
    console.print(f"  Data:   [cyan]{data}[/cyan]")

    if not urls:
        console.print("[yellow]Keine http(s)-URLs im Export gefunden.[/yellow]")
        raise typer.Exit(code=0)

    target_langs: Optional[list[str]] = None
    if lang:
        target_langs = [l.strip().lower() for l in lang.split(",") if l.strip()]
        console.print(f"  Sprache: [cyan]{', '.join(target_langs)}[/cyan]")
    console.print()

    results = collect(urls, data, delay=delay, fresh=fresh, target_langs=target_langs, max_pages=max_pages)
    print_summary(results, data)


@app.command(name="migrate-lang")
def migrate_lang_cmd(
    data: Path = typer.Option(Path(os.environ.get("ILLICO_DATA", "./illico-data")), "--data", "-d", help="Illico-Datenverzeichnis"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Nur anzeigen was passieren wuerde, nichts schreiben"),
):
    """
    Backfillt das language:-Feld im YAML-Frontmatter aller raw/*.md, die es noch nicht haben.
    Idempotent — Dateien mit vorhandenem Feld werden uebersprungen.
    """
    raw_dir = data / "raw"
    if not raw_dir.exists():
        console.print(f"[red]✗ {raw_dir} existiert nicht.[/red]")
        raise typer.Exit(1)

    files = sorted(raw_dir.rglob("*.md"))
    if not files:
        console.print(f"[yellow]Keine raw/*.md gefunden.[/yellow]")
        return

    console.print()
    console.rule("[bold blue]ILLICO MIGRATE-LANG[/bold blue]")
    console.print(f"  Data:    [cyan]{data}[/cyan]")
    console.print(f"  Dateien: [cyan]{len(files)}[/cyan]")
    if dry_run:
        console.print(f"  Modus:   [yellow]dry-run (kein Schreiben)[/yellow]")
    console.print()

    counts = {"updated": 0, "skipped": 0, "no_frontmatter": 0, "unknown_lang": 0}

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            counts["no_frontmatter"] += 1
            continue

        if not text.lstrip().startswith("---"):
            counts["no_frontmatter"] += 1
            continue

        parts = text.split("---", 2)
        if len(parts) < 3:
            counts["no_frontmatter"] += 1
            continue

        fm, body = parts[1], parts[2]
        has_language = any(line.strip().startswith("language:") for line in fm.splitlines())
        if has_language:
            counts["skipped"] += 1
            continue

        detected = detect_language(None, body)
        if not detected:
            counts["unknown_lang"] += 1
            continue

        new_fm = fm.rstrip("\n") + f'\nlanguage: "{detected}"\n'
        new_text = "---" + new_fm + "---" + body

        if not dry_run:
            path.write_text(new_text, encoding="utf-8")
        counts["updated"] += 1

    table = Table(title="[bold]Migration[/bold]", show_header=True)
    table.add_column("Status", style="bold")
    table.add_column("Anzahl", justify="right")
    table.add_row("[green]✓ Aktualisiert[/green]", str(counts["updated"]))
    table.add_row("[cyan]⟳ Schon vorhanden[/cyan]", str(counts["skipped"]))
    table.add_row("[yellow]? Sprache nicht erkannt[/yellow]", str(counts["unknown_lang"]))
    table.add_row("[red]✗ Ohne Frontmatter[/red]", str(counts["no_frontmatter"]))
    console.print(table)


if __name__ == "__main__":
    app()
