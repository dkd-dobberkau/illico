"""Destillat-Schicht: eine Rohseite wird einmal verdichtet und per
Inhalts-Hash gecacht.

Kennt weder Cluster noch Artikel — die Zuordnung lebt in illico_inventory.
"""
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from illico_frontmatter import extract_raw_domain

SCHEMA = 1

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def content_hash(text: str) -> str:
    """SHA-256 ueber Rumpf + Frontmatter OHNE `crawled`.

    `crawled:` aendert sich bei jedem Crawl-Lauf. Zaehlte es mit, wuerde jeder
    Nachcrawl saemtliche Destillate invalidieren und der Cache waere nutzlos.
    Die Frontmatter-Zeilen werden sortiert, damit eine geaenderte Feldreihenfolge
    im Crawler den Cache nicht bricht.
    """
    stripped = text.lstrip("﻿")
    match = _FRONTMATTER.match(stripped)
    if not match:
        payload = stripped.strip()
    else:
        lines = sorted(
            line.strip()
            for line in match.group(1).splitlines()
            if line.strip() and not line.strip().startswith("crawled:")
        )
        body = stripped[match.end():].strip()
        payload = "\n".join(lines) + "\n\n" + body
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DistillStore:
    """Content-adressierter Destillat-Cache: ein JSON pro Seiteninhalt.

    Eine Datei pro Hash statt eines Sammel-JSON: parallele Worker schreiben
    dann in verschiedene Dateien ohne Lock, und ein Abbruch verliert nur die
    Batches, die gerade in der Luft waren.
    """

    def __init__(self, root: Path, schema: int = SCHEMA):
        self.dir = Path(root) / f"v{schema}"

    def _path(self, digest: str) -> Path:
        return self.dir / (digest.removeprefix("sha256:") + ".json")

    def has(self, digest: str) -> bool:
        return self.get(digest) is not None

    def get(self, digest: str) -> dict | None:
        path = self._path(digest)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Kaputte Datei = Cache-Miss. Die Seite wird neu destilliert,
            # statt den ganzen Lauf an einem halb geschriebenen JSON zu killen.
            return None

    def put(self, distillate: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(distillate["hash"])
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(distillate, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)


@dataclass
class DistillResult:
    distillates: dict[str, dict] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    # Je gescheitertem Batch bzw. ausgelassener Seite eine lesbare Zeile mit
    # Ursache und betroffenen Quellen. Ohne die ist ein Fehlschlag nicht
    # zuzuordnen: `failed` enthaelt nur Hashes, und ein gekippter Batch sieht
    # darin genauso aus wie eine einzeln ausgelassene Seite.
    errors: list[str] = field(default_factory=list)


def group_pages(raw_files: dict[str, str]) -> dict[str, dict]:
    """{rel: content} → {hash: {hash, sources, domain, content}}.

    Inhaltsgleiche Seiten unter verschiedenen Pfaden teilen sich einen Hash und
    damit ein Destillat — das spart Calls auf Sites mit vielen Dubletten.
    """
    from illico_compile import _extract_frontmatter_language

    groups: dict[str, dict] = {}
    for rel in sorted(raw_files):
        content = raw_files[rel]
        digest = content_hash(content)
        entry = groups.get(digest)
        if entry is None:
            groups[digest] = {
                "hash": digest,
                "sources": [rel],
                "domain": extract_raw_domain(content) or "",
                # Ohne die Quellsprache entstehen aus englischen Seiten deutsche
                # Destillate und daraus deutsche Artikel.
                "language": _extract_frontmatter_language(content) or "",
                "content": content,
            }
        else:
            entry["sources"].append(rel)
    return groups


def _strip_frontmatter(text: str) -> str:
    match = _FRONTMATTER.match(text.lstrip("﻿"))
    return text[match.end():].strip() if match else text.strip()


def _build_batch_prompt(prompt: str, batch: list[dict], max_chars: int = 4000) -> str:
    parts = [prompt]
    for index, page in enumerate(batch):
        lang = page.get("language") or "unbekannt"
        parts.append(f"### PAGE p{index} (Sprache: {lang})")
        parts.append(_strip_frontmatter(page["content"])[:max_chars])
        parts.append("")
    return "\n".join(parts)


def _parse_batch(response: str) -> dict[str, dict]:
    from illico_compile import parse_llm_json  # lokal: vermeidet Import-Zyklus

    data = parse_llm_json(response) or {}
    return {p["id"]: p for p in data.get("pages", []) if isinstance(p, dict) and p.get("id")}


def _batch_sources(batch, limit: int = 3) -> str:
    """Kurze, lesbare Kennzeichnung der Seiten eines Batches.

    Hashes sind fuer die Fehlersuche wertlos — der Pfad sagt, welche Seite
    betroffen ist. Bei grossen Batches reichen die ersten paar plus Anzahl.
    """
    namen = [page["sources"][0] if page.get("sources") else page["hash"]
             for page in batch]
    if len(namen) <= limit:
        return ", ".join(namen)
    return f"{', '.join(namen[:limit])} … (+{len(namen) - limit} weitere)"


def _distill_batch(batch, model, prompt, call) -> tuple[dict, list, list]:
    try:
        response = call(_build_batch_prompt(prompt, batch), model, 8192)
        parsed = _parse_batch(response)
    except Exception as exc:
        # Ein kaputter Batch darf den Lauf nicht killen. Die Seiten bleiben
        # ohne Destillat und werden beim naechsten Lauf erneut versucht — aber
        # die Ursache muss mit, sonst ist nicht zu erkennen, ob der Fehler
        # transient (Rate-Limit, Timeout) oder deterministisch ist. Bei einem
        # deterministischen Fehler holt kein Folgelauf die Seiten je nach.
        return {}, [page["hash"] for page in batch], [
            f"Batch mit {len(batch)} Seiten ({_batch_sources(batch)}): "
            f"{type(exc).__name__}: {exc}"
        ]

    made: dict[str, dict] = {}
    failed: list[str] = []
    errors: list[str] = []
    now = datetime.now().isoformat(timespec="seconds")
    for index, page in enumerate(batch):
        item = parsed.get(f"p{index}")
        if not item:
            failed.append(page["hash"])
            errors.append(
                f"{_batch_sources([page])}: fehlt in der Modellantwort "
                "(moeglicherweise abgeschnitten)"
            )
            continue
        made[page["hash"]] = {
            "schema": SCHEMA,
            "hash": page["hash"],
            "sources": page["sources"],
            "domain": page["domain"],
            "language": page.get("language", ""),
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "keypoints": item.get("keypoints", []),
            "entities": item.get("entities", []),
            "edges": item.get("edges", []),
            "model": model,
            "created": now,
        }
    return made, failed, errors


def distill_all(
    raw_files: dict[str, str],
    store: DistillStore,
    model: str,
    prompt: str,
    call,
    jobs: int = 4,
    batch_size: int = 15,
) -> DistillResult:
    """Destilliert alle Seiten, die noch nicht im Store liegen.

    `call(prompt, model, max_tokens) -> str` wird injiziert, damit Tests ohne
    Netzwerk und ohne Monkeypatching auskommen.
    """
    groups = group_pages(raw_files)
    result = DistillResult()

    todo = []
    for digest, page in groups.items():
        cached = store.get(digest)
        if cached is not None:
            # sources koennen sich geaendert haben (Seite unter neuem Pfad),
            # der Inhalt nicht — deshalb aktualisieren statt neu destillieren.
            cached["sources"] = page["sources"]
            result.distillates[digest] = cached
        else:
            todo.append(page)

    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    if not batches:
        return result

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [
            pool.submit(_distill_batch, batch, model, prompt, call)
            for batch in batches
        ]
        for future in futures:
            made, failed, errors = future.result()
            for digest, distillate in made.items():
                store.put(distillate)
                result.distillates[digest] = distillate
            result.failed.extend(failed)
            result.errors.extend(errors)

    return result
