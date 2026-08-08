"""Reine Funktionen des Dokument-Ingests: Weiche, Namensbildung, Frontmatter."""
from pathlib import Path

import pytest

import illico_documents as docs


def test_leere_seite_ist_nicht_ausreichend():
    assert docs.is_text_sufficient("", 200) is False
    assert docs.is_text_sufficient("   \n\t  ", 200) is False


def test_schwelle_wird_exakt_eingehalten():
    assert docs.is_text_sufficient("x" * 199, 200) is False
    assert docs.is_text_sufficient("x" * 200, 200) is True
    assert docs.is_text_sufficient("x" * 201, 200) is True


def test_umgebender_whitespace_zaehlt_nicht_mit():
    assert docs.is_text_sufficient("  " + "x" * 199 + "  ", 200) is False


def test_slug_unterscheidet_gleichnamige_dateien_in_unterordnern():
    root = Path("/bestand")
    a = docs.document_slug(Path("/bestand/2024/bericht.pdf"), root)
    b = docs.document_slug(Path("/bestand/2025/bericht.pdf"), root)
    assert a != b
    assert "bericht" in a and "bericht" in b


def test_slug_ist_ueber_laeufe_stabil():
    root = Path("/bestand")
    p = Path("/bestand/handbuch.pdf")
    assert docs.document_slug(p, root) == docs.document_slug(p, root)


def test_seitennummer_wird_auf_dokumentbreite_gepolstert():
    assert docs.page_filename("handbuch", 7, 9) == "handbuch--s7.md"
    assert docs.page_filename("handbuch", 7, 312) == "handbuch--s007.md"
    assert docs.page_filename("handbuch", 312, 312) == "handbuch--s312.md"


def test_frontmatter_traegt_label_als_domain():
    fm = docs.build_page_frontmatter(
        title="Betriebshandbuch", rel_source="a/b.pdf",
        page_no=47, label="handbuecher", language="de",
    )
    assert 'domain: "handbuecher"' in fm
    assert 'language: "de"' in fm
    assert "Seite 47" in fm
    assert "#page=47" in fm
    assert fm.startswith("---\n") and fm.rstrip().endswith("---")


def test_frontmatter_ohne_sprache_laesst_das_feld_weg():
    fm = docs.build_page_frontmatter(
        title="T", rel_source="b.pdf", page_no=1, label="l", language=None,
    )
    assert "language:" not in fm


@pytest.mark.parametrize("label", [
    "../../etc",
    "a/b",
    "a\\b",
    "..",
    "",
])
def test_label_als_pfadausbruch_wird_abgewiesen(label):
    """Finding 10: --label geht ungeprueft in `data / 'raw' / label` und in
    den Manifest-Schluessel `f'{label}/...'` ein. Ein Label mit '/', '\\'
    oder '..' koennte ausserhalb des Datenverzeichnisses schreiben bzw. macht
    den --fresh-Praefix-Filter zweideutig."""
    with pytest.raises(ValueError):
        docs.validate_label(label)


def test_harmloses_label_wird_akzeptiert():
    docs.validate_label("handbuecher")
    docs.validate_label("handbuecher-2026")
