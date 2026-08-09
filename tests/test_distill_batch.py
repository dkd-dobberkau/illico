import json
from pathlib import Path

from illico_distill import DistillStore, distill_all, group_pages

_PROMPT = "Destilliere:\n"


def _page(title: str, body: str, domain: str = "example.com", crawled: str = "2026-08-06") -> str:
    return (
        f'---\ntitle: "{title}"\ndomain: "{domain}"\ncrawled: "{crawled}"\n---\n\n{body}\n'
    )


def _response_for(pages: list[dict]) -> str:
    """Baut eine gueltige LLM-Antwort fuer die uebergebenen Seiten."""
    return json.dumps({
        "pages": [
            {
                "id": p["id"],
                "title": f"T-{p['id']}",
                "summary": f"S-{p['id']}",
                "keypoints": ["k"],
                "entities": [{"name": f"E-{p['id']}", "label": "Ding", "props": {}}],
                "edges": [],
            }
            for p in pages
        ]
    })


class RecordingCall:
    """Fake-LLM: zaehlt Aufrufe und antwortet schematisch."""

    def __init__(self):
        self.calls = 0
        self.seen_page_counts = []

    def __call__(self, prompt: str, model: str, max_tokens: int = 2000) -> str:
        self.calls += 1
        ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### PAGE ")]
        self.seen_page_counts.append(len(ids))
        return _response_for([{"id": i} for i in ids])


def test_group_pages_dedupes_identical_content():
    raw = {
        "a/seite.md": _page("Gleich", "Selber Text"),
        "b/seite.md": _page("Gleich", "Selber Text"),
        "c/andere.md": _page("Anders", "Anderer Text"),
    }
    groups = group_pages(raw)
    assert len(groups) == 2
    dupe = next(g for g in groups.values() if len(g["sources"]) == 2)
    assert sorted(dupe["sources"]) == ["a/seite.md", "b/seite.md"]


def test_group_pages_ignores_crawl_date_difference():
    raw = {
        "a.md": _page("X", "Text", crawled="2026-08-06"),
        "b.md": _page("X", "Text", crawled="2026-09-01"),
    }
    assert len(group_pages(raw)) == 1


def test_first_run_distills_everything(tmp_path: Path):
    raw = {f"s{i}.md": _page(f"T{i}", f"Body {i}") for i in range(5)}
    call = RecordingCall()
    store = DistillStore(tmp_path / "d")

    result = distill_all(raw, store, "test-model", _PROMPT, call, jobs=1, batch_size=15)

    assert len(result.distillates) == 5
    assert result.failed == []
    assert call.calls == 1


def test_second_run_is_free(tmp_path: Path):
    """Der ganze Sinn der Uebung: unveraenderte Seiten kosten keinen Call."""
    raw = {f"s{i}.md": _page(f"T{i}", f"Body {i}") for i in range(5)}
    store = DistillStore(tmp_path / "d")
    distill_all(raw, store, "test-model", _PROMPT, RecordingCall(), jobs=1)

    second = RecordingCall()
    result = distill_all(raw, store, "test-model", _PROMPT, second, jobs=1)

    assert second.calls == 0
    assert len(result.distillates) == 5


def test_only_changed_page_is_redistilled(tmp_path: Path):
    raw = {f"s{i}.md": _page(f"T{i}", f"Body {i}") for i in range(5)}
    store = DistillStore(tmp_path / "d")
    distill_all(raw, store, "test-model", _PROMPT, RecordingCall(), jobs=1)

    raw["s2.md"] = _page("T2", "Body 2 GEAENDERT")
    second = RecordingCall()
    distill_all(raw, store, "test-model", _PROMPT, second, jobs=1)

    assert second.calls == 1
    assert second.seen_page_counts == [1]


def test_recrawl_without_changes_costs_nothing(tmp_path: Path):
    raw = {"s.md": _page("T", "Body", crawled="2026-08-06")}
    store = DistillStore(tmp_path / "d")
    distill_all(raw, store, "test-model", _PROMPT, RecordingCall(), jobs=1)

    raw["s.md"] = _page("T", "Body", crawled="2026-09-01")
    second = RecordingCall()
    distill_all(raw, store, "test-model", _PROMPT, second, jobs=1)

    assert second.calls == 0


def test_failing_batch_does_not_kill_the_run(tmp_path: Path):
    raw = {f"s{i}.md": _page(f"T{i}", f"Body {i}") for i in range(30)}
    store = DistillStore(tmp_path / "d")

    class FlakyCall(RecordingCall):
        def __call__(self, prompt, model, max_tokens=2000):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("LLM kaputt")
            ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### PAGE ")]
            return _response_for([{"id": i} for i in ids])

    result = distill_all(raw, store, "test-model", _PROMPT, FlakyCall(), jobs=1, batch_size=15)

    assert len(result.failed) == 15
    assert len(result.distillates) == 15


def test_failed_pages_are_retried_next_run(tmp_path: Path):
    raw = {f"s{i}.md": _page(f"T{i}", f"Body {i}") for i in range(30)}
    store = DistillStore(tmp_path / "d")

    class OnceFlaky(RecordingCall):
        def __call__(self, prompt, model, max_tokens=2000):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("LLM kaputt")
            ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### PAGE ")]
            return _response_for([{"id": i} for i in ids])

    distill_all(raw, store, "test-model", _PROMPT, OnceFlaky(), jobs=1, batch_size=15)

    healed = RecordingCall()
    result = distill_all(raw, store, "test-model", _PROMPT, healed, jobs=1, batch_size=15)

    assert result.failed == []
    assert len(result.distillates) == 30
    assert healed.calls == 1


def test_page_missing_from_response_counts_as_failed(tmp_path: Path):
    raw = {f"s{i}.md": _page(f"T{i}", f"Body {i}") for i in range(3)}
    store = DistillStore(tmp_path / "d")

    def lossy(prompt, model, max_tokens=2000):
        ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### PAGE ")]
        return _response_for([{"id": i} for i in ids[:-1]])

    result = distill_all(raw, store, "test-model", _PROMPT, lossy, jobs=1)

    assert len(result.failed) == 1
    assert len(result.distillates) == 2


def test_distillate_carries_domain_and_sources(tmp_path: Path):
    raw = {"ordner/seite.md": _page("T", "Body", domain="kunde-b.de")}
    store = DistillStore(tmp_path / "d")
    result = distill_all(raw, store, "test-model", _PROMPT, RecordingCall(), jobs=1)

    d = next(iter(result.distillates.values()))
    assert d["domain"] == "kunde-b.de"
    assert d["sources"] == ["ordner/seite.md"]
    assert d["model"] == "test-model"


def test_parallel_jobs_produce_same_result(tmp_path: Path):
    raw = {f"s{i}.md": _page(f"T{i}", f"Body {i}") for i in range(40)}
    store = DistillStore(tmp_path / "d")
    result = distill_all(raw, store, "test-model", _PROMPT, RecordingCall(), jobs=4, batch_size=10)

    assert len(result.distillates) == 40
    assert result.failed == []


def test_distillate_carries_source_language(tmp_path: Path):
    """Ohne die Sprache der Quellseite entstehen aus englischen Seiten deutsche
    Destillate und daraus deutsche Artikel — die alte Pipeline schrieb Artikel
    in der Sprache der Quellen."""
    raw = {"s.md": '---\ntitle: "T"\ndomain: "example.com"\ncrawled: "2026-08-06"\n'
                   'language: "en"\n---\n\nBody\n'}
    store = DistillStore(tmp_path / "d")
    result = distill_all(raw, store, "test-model", _PROMPT, RecordingCall(), jobs=1)

    d = next(iter(result.distillates.values()))
    assert d["language"] == "en"


def test_batch_prompt_states_the_page_language(tmp_path: Path):
    """Die Sprache muss im Prompt stehen, sonst kann das LLM sie nicht halten."""
    raw = {"s.md": '---\ntitle: "T"\ndomain: "example.com"\nlanguage: "en"\n---\n\nBody\n'}
    store = DistillStore(tmp_path / "d")
    seen = {}

    def capture(prompt, model, max_tokens=2000):
        seen["prompt"] = prompt
        ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### PAGE ")]
        return _response_for([{"id": i} for i in ids])

    distill_all(raw, store, "test-model", _PROMPT, capture, jobs=1)
    assert "en" in seen["prompt"].split("### PAGE p0")[1].splitlines()[0]


def test_missing_language_does_not_break(tmp_path: Path):
    raw = {"s.md": '---\ntitle: "T"\ndomain: "example.com"\n---\n\nBody\n'}
    store = DistillStore(tmp_path / "d")
    result = distill_all(raw, store, "test-model", _PROMPT, RecordingCall(), jobs=1)

    assert next(iter(result.distillates.values()))["language"] == ""


def test_kaputter_batch_meldet_die_ursache(tmp_path: Path):
    """Erster echter Compile-Lauf (450 Seiten, 2026-08-09): 87 Seiten kamen
    ohne Destillat zurueck, im Log standen 4 Ursachen. `_distill_batch` fing
    jede Ausnahme mit einem nackten `except Exception` und gab nur die Hashes
    zurueck — ein gekippter Batch nahm 15 Seiten mit und hinterliess keine
    Spur. Ohne Ursache im Ergebnis ist nicht diagnostizierbar, warum ein Batch
    scheitert, und die Zusage "der naechste Lauf versucht sie erneut" laesst
    sich nicht pruefen.
    """
    raw = {f"s{i}.md": _page(f"T{i}", f"Body {i}") for i in range(3)}
    store = DistillStore(tmp_path / "d")

    def boom(prompt, model, max_tokens=2000):
        raise RuntimeError("Verbindung abgebrochen")

    result = distill_all(raw, store, "test-model", _PROMPT, boom, jobs=1)

    assert len(result.failed) == 3
    assert result.errors, "ein gescheiterter Batch muss eine Ursache hinterlassen"
    bericht = " ".join(result.errors)
    assert "RuntimeError" in bericht, f"Ausnahmetyp fehlt: {bericht!r}"
    assert "Verbindung abgebrochen" in bericht, f"Meldung fehlt: {bericht!r}"
    assert ".md" in bericht, (
        f"die betroffenen Seiten muessen benannt sein, sonst ist der Fehler "
        f"nicht zuzuordnen: {bericht!r}"
    )


def test_fehlende_seite_in_der_antwort_wird_benannt(tmp_path: Path):
    """Der zweite stumme Pfad: das Modell antwortet, laesst aber eine Seite
    aus (abgeschnittenes JSON). Bisher landete sie wortlos in `failed` und war
    von einem gekippten Batch nicht zu unterscheiden.
    """
    raw = {f"s{i}.md": _page(f"T{i}", f"Body {i}") for i in range(3)}
    store = DistillStore(tmp_path / "d")

    def lossy(prompt, model, max_tokens=2000):
        ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### PAGE ")]
        return _response_for([{"id": i} for i in ids[:-1]])

    result = distill_all(raw, store, "test-model", _PROMPT, lossy, jobs=1)

    assert len(result.failed) == 1
    assert len(result.errors) == 1, (
        f"genau die eine ausgelassene Seite muss gemeldet werden (war: {result.errors})"
    )
    assert ".md" in result.errors[0], (
        f"die Meldung muss die Seite benennen: {result.errors[0]!r}"
    )


class _TruncatingCall:
    """Antwortet nur fuer die ersten `grenze` Seiten eines Batches — so
    verhaelt sich ein an max_tokens abgeschnittenes JSON. Kleinere Batches
    gehen vollstaendig durch.
    """

    def __init__(self, grenze: int = 6):
        self.grenze = grenze
        self.calls = 0
        self.batch_groessen: list[int] = []

    def __call__(self, prompt: str, model: str, max_tokens: int = 2000) -> str:
        self.calls += 1
        ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### PAGE ")]
        self.batch_groessen.append(len(ids))
        return _response_for([{"id": i} for i in ids[:self.grenze]])


def test_abgeschnittene_antwort_wird_in_kleineren_batches_nachgeholt(tmp_path: Path):
    """Dritter Compile-Lauf ueber 450 Seiten (2026-08-09): die Seiten 350-353
    meldeten "fehlt in der Modellantwort". Bei batch_size=15 und
    max_tokens=8192 reisst das Destillat-JSON hinten ab, und es fallen immer
    die letzten Seiten eines Batches weg — deterministisch. Der naechste Lauf
    baut denselben Batch und reisst an derselben Stelle ab, die Zusage "der
    naechste Lauf versucht sie erneut" wird also nie eingeloest.

    Mit weniger Seiten pro Aufruf passt das JSON. Der Batch muss sich deshalb
    selbst halbieren, statt die Seiten liegen zu lassen.
    """
    raw = {f"s{i:02d}.md": _page(f"T{i}", f"Body {i}") for i in range(10)}
    store = DistillStore(tmp_path / "d")
    call = _TruncatingCall(grenze=6)

    result = distill_all(raw, store, "test-model", _PROMPT, call, jobs=1, batch_size=10)

    assert result.failed == [], (
        f"abgeschnittene Seiten muessen in kleineren Batches nachkommen "
        f"(offen: {len(result.failed)}, Batch-Groessen: {call.batch_groessen})"
    )
    assert len(result.distillates) == 10
    assert max(call.batch_groessen[1:], default=0) < 10, (
        "der Nachschlag muss kleiner sein als der gescheiterte Batch, sonst "
        f"reisst er an derselben Stelle ab: {call.batch_groessen}"
    )


def test_leere_antwort_loest_keinen_nachschlag_aus(tmp_path: Path):
    """Kostenschutz: kommt keine einzige Seite durch, ist die Antwort nicht
    abgeschnitten, sondern der Aufruf als Ganzes unbrauchbar. Ein Nachschlag
    mit halbierten Batches wuerde den Fehlschlag nur vervielfachen — aus einem
    bezahlten Aufruf wuerden bei batch_size=15 schnell ein Dutzend.
    """
    raw = {f"s{i}.md": _page(f"T{i}", f"Body {i}") for i in range(4)}
    store = DistillStore(tmp_path / "d")
    call = _TruncatingCall(grenze=0)   # nie eine Seite in der Antwort

    result = distill_all(raw, store, "test-model", _PROMPT, call, jobs=1, batch_size=4)

    assert len(result.failed) == 4
    assert call.calls == 1, (
        f"ohne eine einzige geglueckte Seite darf nicht nachgereicht werden "
        f"(war: {call.calls} Aufrufe)"
    )
