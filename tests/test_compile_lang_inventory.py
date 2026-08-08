"""Sprachlaeufe duerfen sich nicht gegenseitig das Inventar ueberschreiben.

`--lang de` und `--lang en` legen Wiki, Graph und Destillat-Store getrennt ab.
Teilten sie sich trotzdem ein `_inventory.json`, wuerde `prune()` im zweiten
Lauf alle Cluster der anderen Sprache leeren — deren Artikel blieben als
Waisen liegen und der naechste Lauf der ersten Sprache schnitte die Themen neu.
"""
import json
from pathlib import Path

from typer.testing import CliRunner

import illico_compile

from test_compile_incremental_e2e import ScriptedLLM

runner = CliRunner()


def _write_page(raw: Path, rel: str, body: str, language: str) -> None:
    path = raw / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntitle: "{rel}"\ndomain: "example.com"\n'
        f'crawled: "2026-08-06"\nlanguage: "{language}"\n---\n\n{body}\n',
        encoding="utf-8",
    )


def _run(data: Path, lang: str):
    return runner.invoke(
        illico_compile.app, ["--data", str(data), "--jobs", "1", "--lang", lang]
    )


def _prepare(tmp_path: Path, monkeypatch) -> tuple[Path, ScriptedLLM]:
    data = tmp_path / "illico-data"
    (data / "raw").mkdir(parents=True)
    _write_page(data / "raw", "de.md", "Ein deutscher Text ueber Dinge.", "de")
    _write_page(data / "raw", "en.md", "An English text about things.", "en")

    llm = ScriptedLLM()
    monkeypatch.setattr(illico_compile, "call_llm", llm)
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    return data, llm


def test_each_language_gets_its_own_inventory(tmp_path: Path, monkeypatch):
    data, _ = _prepare(tmp_path, monkeypatch)

    assert _run(data, "de").exit_code == 0
    assert _run(data, "en").exit_code == 0

    assert (data / "_inventory-de.json").exists()
    assert (data / "_inventory-en.json").exists()


def test_second_language_run_leaves_the_first_intact(tmp_path: Path, monkeypatch):
    data, _ = _prepare(tmp_path, monkeypatch)

    _run(data, "de")
    de_before = json.loads((data / "_inventory-de.json").read_text(encoding="utf-8"))
    _run(data, "en")
    de_after = json.loads((data / "_inventory-de.json").read_text(encoding="utf-8"))

    assert de_after == de_before
    assert de_after["clusters"], "der deutsche Lauf hat seine Cluster verloren"


def test_rerun_after_other_language_stays_free(tmp_path: Path, monkeypatch):
    """Der Kern des Schadens: nach dem EN-Lauf muss ein erneuter DE-Lauf
    weiterhin gratis sein. Ueber ein geteiltes Inventar waere er es nicht —
    die Cluster waeren weg, also Zuordnung, Artikel, Index und Lint erneut."""
    data, llm = _prepare(tmp_path, monkeypatch)

    _run(data, "de")
    _run(data, "en")

    llm.calls = 0
    assert _run(data, "de").exit_code == 0
    assert llm.calls == 0
