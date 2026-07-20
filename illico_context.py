"""Request-Sicht (WikiContext) + Single-User-Provider — Kern-Modul (offen).

Der Kern kennt kein Tenant-Konzept. Ein optionales Overlay kann einen eigenen
Provider liefern, der einen WikiContext aus anderer Quelle baut.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class WikiContext:
    data_dir: Path
    wiki_prefix: str
    languages_prefix: str | None
    graph_namespace: str | None
    chat_bucket: str
    chat_list_all: bool
    label: str
    unrestricted: bool
    filter_articles: Callable[[dict[str, str], dict[str, str]], dict[str, str]]
    # Erlaubte Quell-Domains für Domain-Zählung (/api/stats, /api/domains).
    # None = unbeschränkt (Admin/Single-User); sonst das Whitelist-Set des
    # Tenants (entspricht dem alten `tenant_allowed_domains`).
    allowed_domains: set[str] | None = None


def _default_data_dir() -> Path:
    return Path(os.environ.get("ILLICO_DATA", "./illico-data"))


def single_user_provider() -> WikiContext:
    """Feste Single-User-Sicht: wiki/, graph/, Chat-Bucket 'local', volle Sicht."""
    return WikiContext(
        data_dir=_default_data_dir(),
        wiki_prefix="wiki",
        languages_prefix="wiki",
        graph_namespace=None,
        chat_bucket="local",
        chat_list_all=False,
        label="local",
        unrestricted=True,
        filter_articles=lambda articles, raw_domains: dict(articles),
        allowed_domains=None,
    )


def resolve_wiki_dir(ctx: WikiContext, lang: str | None = None) -> Path:
    base = ctx.wiki_prefix
    if lang:
        candidate = ctx.data_dir / f"{base}-{lang.strip().lower()}"
        if candidate.exists():
            return candidate
    return ctx.data_dir / base


def list_wiki_languages(ctx: WikiContext) -> list[dict]:
    """Verfügbare Wiki-Verzeichnisse für die Sicht. languages_prefix=None → []."""
    out: list[dict] = []
    prefix = ctx.languages_prefix
    if prefix is None or not ctx.data_dir.exists():
        return out
    for entry in sorted(ctx.data_dir.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if not (entry / "_index.md").exists() and not any(entry.glob("*.md")):
            continue
        if name == prefix:
            out.append({"lang": "", "dir": name, "default": True})
        elif name.startswith(prefix + "-"):
            out.append({"lang": name[len(prefix) + 1:], "dir": name, "default": False})
    return out
