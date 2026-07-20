"""Tests für illico_crawl_status — Block-Klassifikation und Persistenz."""
import json
from pathlib import Path

import illico_crawl_status as cs


def _results(success=0, failed_reasons=None):
    return {
        "success": [(f"https://x/{i}", f"{i}.md") for i in range(success)],
        "failed": [(f"https://x/f{i}", r) for i, r in enumerate(failed_reasons or [])],
        "skipped": [],
        "cached": [],
    }


def test_persistent_429_is_blocked():
    r = _results(success=0, failed_reasons=["HTTP 429 (Rate-Limit)"] * 8)
    out = cs.classify_block(r)
    assert out["blocked"] is True
    assert out["top_status"] == 429
    assert "429" in out["reason"]
    assert out["ok"] == 0 and out["failed"] == 8


def test_all_403_is_blocked_waf_wording():
    r = _results(success=0, failed_reasons=["HTTP 403"] * 5)
    out = cs.classify_block(r)
    assert out["blocked"] is True
    assert out["top_status"] == 403
    assert "WAF" in out["reason"]


def test_weird_waf_code_247_is_blocked():
    # Manche Server liefern HTTP 247 — numerisch in 2xx, darf trotzdem als Block gelten.
    r = _results(success=0, failed_reasons=["HTTP 247"] * 6)
    out = cs.classify_block(r)
    assert out["blocked"] is True
    assert out["top_status"] == 247


def test_successful_crawl_not_blocked():
    r = _results(success=50, failed_reasons=["HTTP 404"] * 2)
    out = cs.classify_block(r)
    assert out["blocked"] is False
    assert out["reason"] is None


def test_only_skipped_not_blocked():
    # Nicht-HTML/Sprachfilter erzeugen skipped, keine HTTP-Fehler -> nicht blockiert.
    r = {"success": [], "failed": [], "skipped": ["a", "b"], "cached": []}
    out = cs.classify_block(r)
    assert out["blocked"] is False
    assert out["top_status"] is None


def test_below_min_count_not_blocked():
    # Nur 2 Blocks (< BLOCK_MIN_COUNT=3) trotz ok=0 -> nicht blockiert.
    r = _results(success=0, failed_reasons=["HTTP 429", "HTTP 429"])
    out = cs.classify_block(r)
    assert out["blocked"] is False


def test_network_exceptions_do_not_count_as_block():
    # Failed-Einträge ohne "HTTP <code>" (Netzwerk-Exceptions) sind keine Blocks.
    r = _results(success=0, failed_reasons=["ConnectTimeout", "ReadError", "ConnectError"])
    out = cs.classify_block(r)
    assert out["blocked"] is False
    assert out["top_status"] is None


def test_load_missing_returns_empty(tmp_path: Path):
    assert cs.load_crawl_status(tmp_path) == {"domains": {}}


def test_load_corrupt_returns_empty(tmp_path: Path):
    (tmp_path / cs.STATUS_FILE).write_text("{ not json", encoding="utf-8")
    assert cs.load_crawl_status(tmp_path) == {"domains": {}}


def test_save_then_load_roundtrip(tmp_path: Path):
    status = {"domains": {"www.x.de": {"blocked": True, "reason": "Rate-Limit (HTTP 429)", "ok": 0}}}
    cs.save_crawl_status(tmp_path, status)
    loaded = cs.load_crawl_status(tmp_path)
    assert loaded["domains"]["www.x.de"]["blocked"] is True
    assert loaded["domains"]["www.x.de"]["reason"] == "Rate-Limit (HTTP 429)"


def test_save_preserves_other_domains(tmp_path: Path):
    # Erst Domain A schreiben, dann per load->update->save Domain B ergänzen:
    # A muss erhalten bleiben (monotoner Per-Domain-Update).
    cs.save_crawl_status(tmp_path, {"domains": {"a.de": {"blocked": True}}})
    st = cs.load_crawl_status(tmp_path)
    st["domains"]["b.de"] = {"blocked": False}
    cs.save_crawl_status(tmp_path, st)
    loaded = cs.load_crawl_status(tmp_path)
    assert set(loaded["domains"].keys()) == {"a.de", "b.de"}
