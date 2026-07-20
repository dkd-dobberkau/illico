from illico_compile import _filter_raw_by_domains


def test_only_domains_keeps_matching():
    raw = {
        "a.md": "---\ndomain: keep.com\n---\nx",
        "b.md": "---\ndomain: drop.com\n---\ny",
        "c.md": "---\nsource_url: https://keep.com/z\n---\nz",
    }
    out = _filter_raw_by_domains(raw, {"keep.com"})
    assert set(out) == {"a.md", "c.md"}


def test_empty_filter_returns_all():
    raw = {"a.md": "---\ndomain: keep.com\n---\nx"}
    assert _filter_raw_by_domains(raw, None) == raw
