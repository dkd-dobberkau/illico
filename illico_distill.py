"""Destillat-Schicht: eine Rohseite wird einmal verdichtet und per
Inhalts-Hash gecacht.

Kennt weder Cluster noch Artikel — die Zuordnung lebt in illico_inventory.
"""
import hashlib
import json
import os
import re
from pathlib import Path

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
