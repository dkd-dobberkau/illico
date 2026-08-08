"""Anbindung an pypdfium2. Ein Smoke-Test mit einem echten Mini-PDF.

Geprueft wird die Verdrahtung, nicht pdfium selbst: dass wir die richtigen
Methoden aufrufen und Bytes zurueckbekommen.
"""
from pathlib import Path

import pytest

import illico_documents as docs

# Von Hand geschriebenes, gueltiges PDF mit einer Seite und Textebene.
MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
5 0 obj<</Length 68>>stream
BT /F1 24 Tf 72 700 Td (Hallo Illico aus einem PDF) Tj ET
endstream
endobj
trailer<</Root 1 0 R>>
"""


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    p = tmp_path / "mini.pdf"
    p.write_bytes(MINIMAL_PDF)
    return p


def test_textebene_wird_gelesen(pdf_path: Path):
    pdf = docs.open_document(pdf_path)
    text = docs.extract_text(pdf[0])
    assert "Hallo Illico" in text


def test_seite_rendert_zu_png_bytes(pdf_path: Path):
    pdf = docs.open_document(pdf_path)
    png = docs.render_page_png(pdf[0], dpi=100)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 100


def test_seitenzahl_stimmt(pdf_path: Path):
    assert len(docs.open_document(pdf_path)) == 1


def test_defektes_pdf_wirft_pdfiumerror(tmp_path: Path):
    import pypdfium2 as pdfium

    bad = tmp_path / "kaputt.pdf"
    bad.write_bytes(b"das ist kein PDF")
    with pytest.raises(pdfium.PdfiumError):
        docs.open_document(bad)


def test_titel_faellt_auf_den_dateinamen_zurueck(pdf_path: Path):
    pdf = docs.open_document(pdf_path)
    assert docs.document_title(pdf, fallback="mini") == "mini"
