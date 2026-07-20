"""Test-Hilfen für Illico: baut Raw-Seiten und Wiki-Artikel in einem
Daten-Verzeichnis auf. Wird von der Illico-Test-Suite (und Downstream-
Konsumenten via `illico[test]`) genutzt. Importiert nur stdlib.
"""
from pathlib import Path


def write_raw(data_dir: Path, rel: str, domain: str, body: str = "Beispiel") -> None:
    path = data_dir / "raw" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {rel}\nsource_url: https://{domain}/{rel}\ndomain: {domain}\n---\n{body}\n",
        encoding="utf-8",
    )


def write_article(data_dir: Path, name: str, sources: list[str], body: str = "Artikel") -> None:
    src_str = ", ".join(f'"{s}"' for s in sources)
    (data_dir / "wiki" / name).write_text(
        f"---\ntitle: {name}\nsources: [{src_str}]\n---\n{body}\n",
        encoding="utf-8",
    )


def build_mini_wiki(data_dir: Path) -> None:
    """Baut ein Wiki mit 2 Domains und 3 Artikeln."""
    write_raw(data_dir, "acme/page1.md", "www.acme.com")
    write_raw(data_dir, "acme/page2.md", "www.acme.com")
    write_raw(data_dir, "kundeb/page1.md", "kunde-b.de")
    write_article(data_dir, "_index.md", [], body="# Wiki-Index\n- [[acme-product]]\n- [[acme-faq]]\n- [[kundeb-info]]")
    write_article(data_dir, "acme-product.md", ["acme/page1.md"])
    write_article(data_dir, "acme-faq.md", ["acme/page2.md"])
    write_article(data_dir, "kundeb-info.md", ["kundeb/page1.md"])
