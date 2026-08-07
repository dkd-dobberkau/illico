from pathlib import Path

import illico_compile
from illico_compile import get_prompts, phase_index


class CapturingCall:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt, model, max_tokens=2000, retries=3):
        self.prompts.append(prompt)
        return "# Index\n"


def test_index_prompt_states_the_source_language(tmp_path: Path, monkeypatch):
    """Die _index.md ist die Einstiegsseite — sie muss dieselbe Sprache haben
    wie die Artikel, sonst begruesst eine englische Wissensbasis auf Deutsch."""
    call = CapturingCall()
    monkeypatch.setattr(illico_compile, "call_llm", call)
    wiki = tmp_path / "wiki"
    wiki.mkdir()

    phase_index({"clusters": []}, [("a", "A")], wiki, "m", get_prompts("de"), lang="en")

    assert "Sprache der Quellen: en" in call.prompts[0]


def test_index_without_language_stays_silent(tmp_path: Path, monkeypatch):
    call = CapturingCall()
    monkeypatch.setattr(illico_compile, "call_llm", call)
    wiki = tmp_path / "wiki"
    wiki.mkdir()

    phase_index({"clusters": []}, [("a", "A")], wiki, "m", get_prompts("de"))

    assert "Sprache der Quellen:" not in call.prompts[0]
