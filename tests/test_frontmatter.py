from illico_frontmatter import extract_sources, extract_raw_domain


def test_extract_sources_inline():
    md = '---\nsources: ["a.md", "b.md"]\n---\n# Titel\n'
    assert extract_sources(md) == ["a.md", "b.md"]


def test_extract_sources_yaml_list():
    md = '---\nsources:\n  - "a.md"\n  - "b.md"\n---\n'
    assert extract_sources(md) == ["a.md", "b.md"]


def test_extract_raw_domain_from_domain_field():
    md = '---\ntitle: X\ndomain: example.com\n---\n'
    assert extract_raw_domain(md) == "example.com"


def test_extract_raw_domain_from_url_fallback():
    md = '---\nsource_url: https://foo.example.org/x\n---\n'
    assert extract_raw_domain(md) == "foo.example.org"
