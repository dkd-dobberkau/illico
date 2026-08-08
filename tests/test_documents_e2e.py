"""CLI-Ebene und Zusammenspiel mit dem Compile."""
from pathlib import Path

from typer.testing import CliRunner

import illico_compile
import illico_documents as docs
import illico_ingest
from test_documents_pdf import MINIMAL_PDF
from test_documents_routing import FakeLLM

runner = CliRunner()


def _bestand(tmp_path: Path) -> Path:
    src = tmp_path / "bestand"
    src.mkdir()
    (src / "handbuch.pdf").write_bytes(MINIMAL_PDF)
    return src


def test_cli_legt_seiten_unter_dem_label_ab(tmp_path, monkeypatch):
    src = _bestand(tmp_path)
    data = tmp_path / "illico-data"
    monkeypatch.setattr(docs, "vision_markdown", lambda png, model, call: "# X\n")

    result = runner.invoke(illico_ingest.app, [
        "documents", str(src), "--data", str(data),
        "--label", "handbuecher", "--jobs", "1",
    ])

    assert result.exit_code == 0, result.output
    assert list((data / "raw" / "handbuecher").glob("*.md"))


def test_cli_meldet_die_bilanz(tmp_path, monkeypatch):
    src = _bestand(tmp_path)
    monkeypatch.setattr(docs, "vision_markdown", lambda png, model, call: "# X\n")

    result = runner.invoke(illico_ingest.app, [
        "documents", str(src), "--data", str(tmp_path / "d"),
        "--label", "l", "--jobs", "1",
    ])

    assert "Textebene" in result.output
    assert "Vision" in result.output


def test_cli_bricht_ohne_dateien_ab(tmp_path):
    leer = tmp_path / "leer"
    leer.mkdir()
    result = runner.invoke(illico_ingest.app, [
        "documents", str(leer), "--data", str(tmp_path / "d"), "--label", "l",
    ])
    assert result.exit_code == 1
    assert "Keine PDF" in result.output


def test_erzeugte_seiten_lassen_sich_kompilieren(tmp_path, monkeypatch):
    """Der eigentliche Zweck: was hier rauskommt, muss der Compile fressen."""
    from test_compile_incremental_e2e import ScriptedLLM

    src = _bestand(tmp_path)
    data = tmp_path / "illico-data"
    docs.ingest_documents(target=src, data=data, label="handbuecher",
                          model="m", jobs=1, call=FakeLLM())

    llm = ScriptedLLM()
    monkeypatch.setattr(illico_compile, "call_llm", llm)
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    result = runner.invoke(illico_compile.app,
                           ["--data", str(data), "--jobs", "1"])

    assert result.exit_code == 0, result.output
    # Nicht nur "nichts abgestuerzt": die Seiten muessen wirklich durch die
    # Destillation gelaufen und in einem Artikel gelandet sein. _index.md
    # allein beweist nichts — der Compile schreibt es beim ersten Lauf immer.
    assert llm.calls > 0, "der Compile hat gar kein Modell angefasst"
    assert list((data / "distill" / "v1").glob("*.json")), "kein Destillat entstanden"
    articles = [p for p in (data / "wiki").glob("*.md")
                if p.name not in ("_index.md", "_lint-report.md")]
    assert articles, "kein Artikel aus den Dokumentseiten entstanden"


def test_only_domains_trifft_genau_das_label(tmp_path, monkeypatch):
    from test_compile_incremental_e2e import ScriptedLLM

    src = _bestand(tmp_path)
    data = tmp_path / "illico-data"
    docs.ingest_documents(target=src, data=data, label="handbuecher",
                          model="m", jobs=1, call=FakeLLM())

    monkeypatch.setattr(illico_compile, "call_llm", ScriptedLLM())
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    result = runner.invoke(illico_compile.app, [
        "--data", str(data), "--jobs", "1", "--only-domains", "handbuecher",
    ])

    assert result.exit_code == 0, result.output
