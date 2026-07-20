"""Erkennung & Persistenz, ob eine gecrawlte Domain den Illico-Crawler blockt.

Stdlib-only, damit sowohl der Crawler (illico_ingest) als auch die FastAPI-App
(illico_app) das Modul importieren können, ohne schwere Crawler-Abhängigkeiten
zu ziehen.
"""
import json
import os
import re
from collections import Counter
from pathlib import Path

# Klassische Bot-/Rate-Limit-Codes — werden in _block_label() für spezifische
# freundliche Beschriftung herangezogen (Codes außerhalb bekommen generisches "HTTP <code>").
# NICHT als Gate in classify_block(), sonst würden unbekannte WAF-Codes wie 247 verpasst.
BLOCK_STATUS = {401, 403, 407, 429, 503}

# Heuristik-Schwellen (justierbar/testbar).
BLOCK_OK_RATIO = 0.05    # so wenig Erfolge gelten als "praktisch keine"
BLOCK_MIN_COUNT = 3      # so viele gleiche Block-Codes mindestens
BLOCK_MAJORITY = 0.5     # dominanter Code muss >= 50% der Failures ausmachen

_HTTP_CODE_RE = re.compile(r"HTTP (\d+)")

STATUS_FILE = "_crawl-status.json"


def _block_label(code: int) -> str:
    """Freundliche Beschriftung je Block-Code. Codes in BLOCK_STATUS bekommen
    eine spezifische Formulierung, alle anderen die generische ``HTTP <code>``."""
    if code == 429:
        return "Rate-Limit (HTTP 429)"
    if code in BLOCK_STATUS:  # verbleibend: 401/403/407/503
        if code == 503:
            return "Dienst nicht verfügbar / Bot-Schutz (HTTP 503)"
        return f"WAF/Bot-Schutz (HTTP {code})"
    return f"HTTP {code}"


def classify_block(results: dict) -> dict:
    """Klassifiziert aus einem Crawl-Ergebnis-Dict, ob die Domain blockt.

    Blockiert = ~keine Erfolge UND ein HTTP-Fehlercode dominiert die Failures.
    """
    ok = len(results.get("success", []))
    failed_list = results.get("failed", [])
    failed = len(failed_list)

    codes: list[int] = []
    for entry in failed_list:
        reason = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else str(entry)
        m = _HTTP_CODE_RE.search(str(reason))
        if m:
            code = int(m.group(1))
            if code != 200:
                codes.append(code)

    none_result = {"blocked": False, "reason": None, "top_status": None, "ok": ok, "failed": failed}
    if not codes:
        return none_result

    top_status, top_n = Counter(codes).most_common(1)[0]
    near_zero_ok = ok == 0 or ok / (ok + failed) < BLOCK_OK_RATIO
    dominant = top_n >= max(BLOCK_MIN_COUNT, BLOCK_MAJORITY * failed)
    if not (near_zero_ok and dominant):
        return none_result

    reason = f"{_block_label(top_status)} — {top_n}/{failed} URLs geblockt"
    return {"blocked": True, "reason": reason, "top_status": top_status, "ok": ok, "failed": failed}


def load_crawl_status(output_dir: Path) -> dict:
    """Lädt _crawl-status.json; bei fehlender/kaputter Datei -> {"domains": {}}."""
    path = Path(output_dir) / STATUS_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("domains"), dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"domains": {}}


def save_crawl_status(output_dir: Path, status: dict) -> None:
    """Speichert den Block-Status als JSON — atomar (temp + os.replace).

    Ohne atomaren Swap könnte ein Absturz mitten im Schreiben die Datei
    zerreißen; load_crawl_status würde dann den Status ALLER Domains verwerfen,
    nicht nur den gerade geschriebenen. temp-Datei + os.replace macht den
    Wechsel atomar (os.replace ist auf POSIX ein atomarer Rename).
    """
    path = Path(output_dir) / STATUS_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
