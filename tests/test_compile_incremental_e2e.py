import json
from pathlib import Path

from typer.testing import CliRunner

import illico_compile

runner = CliRunner()


def _write_page(raw: Path, rel: str, body: str, crawled: str = "2026-08-06") -> None:
    path = raw / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntitle: "{rel}"\ndomain: "example.com"\ncrawled: "{crawled}"\n---\n\n{body}\n',
        encoding="utf-8",
    )


class ScriptedLLM:
    """Beantwortet Destillation, Zuordnung, Artikel, Index und Lint schematisch."""

    def __init__(self):
        self.calls = 0

    def __call__(self, prompt, model, max_tokens=2000, retries=3):
        self.calls += 1
        if "### PAGE " in prompt:
            ids = [l.split()[2] for l in prompt.splitlines() if l.startswith("### PAGE ")]
            return json.dumps({"pages": [
                {"id": i, "title": f"T{i}", "summary": f"S{i}", "keypoints": [],
                 "entities": [{"name": f"E{i}", "label": "Ding", "props": {}}], "edges": []}
                for i in ids
            ]})
        if "### DOC " in prompt:
            ids = [l.split()[2] for l in prompt.splitlines() if l.startswith("### DOC ")]
            return json.dumps({"assignments": [], "new_clusters": [
                {"slug": "alles", "name": "Alles", "description": "d", "members": ids}
            ]})
        return "# Artikel\n\nText.\n"


def _run(data: Path):
    return runner.invoke(illico_compile.app, ["--data", str(data), "--jobs", "1"])


def test_incremental_second_run_is_nearly_free(tmp_path: Path, monkeypatch):
    data = tmp_path / "illico-data"
    (data / "raw").mkdir(parents=True)
    for i in range(6):
        _write_page(data / "raw", f"ordner/s{i}.md", f"Body {i}")

    llm = ScriptedLLM()
    monkeypatch.setattr(illico_compile, "call_llm", llm)
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)

    first = _run(data)
    assert first.exit_code == 0, first.output
    assert llm.calls > 0

    llm.calls = 0
    second = _run(data)
    assert second.exit_code == 0, second.output
    assert llm.calls == 0


def test_new_page_touches_only_its_cluster(tmp_path: Path, monkeypatch):
    data = tmp_path / "illico-data"
    (data / "raw").mkdir(parents=True)
    for i in range(3):
        _write_page(data / "raw", f"s{i}.md", f"Body {i}")

    llm = ScriptedLLM()
    monkeypatch.setattr(illico_compile, "call_llm", llm)
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    _run(data)

    _write_page(data / "raw", "s3.md", "Ganz neuer Body")
    llm.calls = 0
    result = _run(data)

    assert result.exit_code == 0
    # 1 Destillations-Batch + 1 Zuordnungs-Batch + 1 Artikel + Index + Lint
    assert llm.calls <= 5


def test_recrawl_without_content_change_costs_nothing(tmp_path: Path, monkeypatch):
    data = tmp_path / "illico-data"
    (data / "raw").mkdir(parents=True)
    _write_page(data / "raw", "s.md", "Body", crawled="2026-08-06")

    llm = ScriptedLLM()
    monkeypatch.setattr(illico_compile, "call_llm", llm)
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    _run(data)

    _write_page(data / "raw", "s.md", "Body", crawled="2026-09-01")
    llm.calls = 0
    _run(data)

    assert llm.calls == 0


def test_legacy_inventory_triggers_rebuild(tmp_path: Path, monkeypatch):
    data = tmp_path / "illico-data"
    (data / "raw").mkdir(parents=True)
    _write_page(data / "raw", "s.md", "Body")
    (data / "_inventory.json").write_text(
        json.dumps({"clusters": [{"slug": "alt", "name": "Alt", "files": ["s.md"]}]}),
        encoding="utf-8",
    )

    llm = ScriptedLLM()
    monkeypatch.setattr(illico_compile, "call_llm", llm)
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    result = _run(data)

    assert result.exit_code == 0
    inv = json.loads((data / "_inventory.json").read_text(encoding="utf-8"))
    assert inv["schema"] == 1
    assert "members" in inv["clusters"][0]


def test_distill_store_lives_next_to_the_wiki(tmp_path: Path, monkeypatch):
    data = tmp_path / "illico-data"
    (data / "raw").mkdir(parents=True)
    _write_page(data / "raw", "s.md", "Body")

    monkeypatch.setattr(illico_compile, "call_llm", ScriptedLLM())
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    _run(data)

    assert list((data / "distill" / "v1").glob("*.json"))


def test_failed_pages_are_reported(tmp_path: Path, monkeypatch):
    data = tmp_path / "illico-data"
    (data / "raw").mkdir(parents=True)
    for i in range(3):
        _write_page(data / "raw", f"s{i}.md", f"Body {i}")

    class BrokenDistill(ScriptedLLM):
        def __call__(self, prompt, model, max_tokens=2000, retries=3):
            if "### PAGE " in prompt:
                raise RuntimeError("LLM kaputt")
            return super().__call__(prompt, model, max_tokens, retries)

    monkeypatch.setattr(illico_compile, "call_llm", BrokenDistill())
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    result = _run(data)

    assert result.exit_code == 0
    assert "ohne Destillat" in result.output
