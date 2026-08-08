"""Die Weiche: Textseiten kosten nichts, Scans gehen ueber Vision.

Gestubbt wird an der Naht `extract_text`. Ob pdfium bei einem echten Scan
wirklich leeren Text liefert, ist pdfiums Sache und nicht unsere.
"""
import pytest

import illico_documents as docs


class FakeLLM:
    """Zaehlt Aufrufe und liefert festes Markdown."""

    def __init__(self, answer="# Aus dem Bild\n\nText.\n"):
        self.calls = 0
        self.answer = answer
        self.last_messages = None

    def __call__(self, model, messages, system=None, max_tokens=2000, retries=3):
        self.calls += 1
        self.last_messages = messages
        return self.answer


class FakePage:
    """Ersetzt eine pdfium-Seite. Wird nur durchgereicht, nie benutzt."""


def _patch(monkeypatch, text: str):
    monkeypatch.setattr(docs, "extract_text", lambda page: text)
    monkeypatch.setattr(docs, "render_page_png", lambda page, dpi: b"\x89PNG-fake")


def _run(monkeypatch, text, llm, threshold=200, force_vision=False):
    """Beide Haelften nacheinander — so setzt der Treiber sie auch zusammen."""
    _patch(monkeypatch, text)
    prepared = docs.prepare_page(FakePage(), page_no=1, threshold=threshold,
                                 force_vision=force_vision, dpi=200)
    return prepared, docs.finish_page(prepared, model="m", call=llm)


def test_textseite_kostet_keinen_llm_aufruf(monkeypatch):
    llm = FakeLLM()
    prepared, (markdown, via_vision) = _run(monkeypatch, "x" * 500, llm)

    assert llm.calls == 0
    assert via_vision is False
    assert markdown.strip() == "x" * 500
    assert prepared.png is None, "eine Textseite darf gar nicht erst gerendert werden"


def test_scanseite_geht_genau_einmal_ans_modell(monkeypatch):
    llm = FakeLLM()
    prepared, (markdown, via_vision) = _run(monkeypatch, "", llm)

    assert llm.calls == 1
    assert via_vision is True
    assert "Aus dem Bild" in markdown
    assert prepared.markdown is None


def test_force_vision_schickt_auch_textseiten_ans_modell(monkeypatch):
    llm = FakeLLM()
    _, (_, via_vision) = _run(monkeypatch, "x" * 500, llm, force_vision=True)

    assert llm.calls == 1
    assert via_vision is True


def test_schwelle_verschiebt_die_weiche(monkeypatch):
    llm = FakeLLM()
    _run(monkeypatch, "x" * 300, llm, threshold=500)

    assert llm.calls == 1, "300 Zeichen unter Schwelle 500 muessen ueber Vision gehen"


def test_prepare_page_macht_keinen_netzaufruf(monkeypatch):
    """Die Trennung ist der Zweck: prepare_page laeuft im Hauptthread."""
    _patch(monkeypatch, "")
    llm = FakeLLM()

    docs.prepare_page(FakePage(), page_no=1, threshold=200,
                      force_vision=False, dpi=200)

    assert llm.calls == 0


def test_seitennummer_ueberlebt_die_vorbereitung(monkeypatch):
    _patch(monkeypatch, "")
    prepared = docs.prepare_page(FakePage(), page_no=47, threshold=200,
                                 force_vision=False, dpi=200)
    assert prepared.page_no == 47


def test_bild_wird_als_data_uri_geschickt(monkeypatch):
    llm = FakeLLM()
    _run(monkeypatch, "", llm)

    content = llm.last_messages[0]["content"]
    image_block = next(b for b in content if b["type"] == "image_url")
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")


def test_leere_modellantwort_gilt_als_fehlschlag(monkeypatch):
    llm = FakeLLM(answer="   \n  ")

    with pytest.raises(docs.PageExtractionError):
        _run(monkeypatch, "", llm)
