from illico_distill import content_hash

_BASE = (
    '---\n'
    'title: "Seite A"\n'
    'source_url: "https://example.com/a"\n'
    'domain: "example.com"\n'
    'crawled: "2026-08-06"\n'
    'language: "de"\n'
    '---\n'
    '\n'
    'Inhalt der Seite.\n'
)


def test_same_content_same_hash():
    assert content_hash(_BASE) == content_hash(_BASE)


def test_crawled_date_is_ignored():
    """Der Crawler schreibt bei JEDEM Lauf ein neues `crawled:`-Datum. Zaehlte
    es mit, waere der Cache nach jedem Nachcrawl komplett wertlos."""
    later = _BASE.replace('"2026-08-06"', '"2026-09-01"')
    assert content_hash(later) == content_hash(_BASE)


def test_body_change_changes_hash():
    changed = _BASE.replace("Inhalt der Seite.", "Ganz anderer Inhalt.")
    assert content_hash(changed) != content_hash(_BASE)


def test_title_change_changes_hash():
    changed = _BASE.replace('"Seite A"', '"Seite B"')
    assert content_hash(changed) != content_hash(_BASE)


def test_frontmatter_field_order_is_irrelevant():
    reordered = (
        '---\n'
        'domain: "example.com"\n'
        'title: "Seite A"\n'
        'crawled: "2026-08-06"\n'
        'source_url: "https://example.com/a"\n'
        'language: "de"\n'
        '---\n'
        '\n'
        'Inhalt der Seite.\n'
    )
    assert content_hash(reordered) == content_hash(_BASE)


def test_file_without_frontmatter_still_hashes():
    assert content_hash("Nur Text, kein Frontmatter.\n").startswith("sha256:")


def test_hash_has_prefix():
    assert content_hash(_BASE).startswith("sha256:")
