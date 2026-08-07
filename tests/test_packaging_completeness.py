"""Jedes Kern-Modul muss auch im Paket landen.

`[tool.hatch.build.targets.wheel] only-include` ist eine explizite Whitelist.
Ein neues Modul, das dort vergessen wird, faellt beim Test nicht auf — die
Suite laeuft gegen den Quellbaum, nicht gegen das gebaute Paket. Genau so ging
v0.3.0 ohne illico_distill.py und illico_inventory.py raus.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _whitelisted_modules() -> set[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("[tool.hatch.build.targets.wheel]", 1)[1]
    block = block.split("sources", 1)[0]
    return set(re.findall(r"illico_[a-z_0-9]+\.py", block))


def _root_modules() -> set[str]:
    return {p.name for p in ROOT.glob("illico_*.py")}


def test_every_core_module_is_packaged():
    missing = _root_modules() - _whitelisted_modules()
    assert not missing, (
        "Diese Module liegen im Repo, werden aber nicht ins Wheel gepackt "
        f"und fehlen nach der Installation: {sorted(missing)}"
    )


def test_whitelist_has_no_dead_entries():
    stale = _whitelisted_modules() - _root_modules()
    assert not stale, f"Whitelist nennt nicht mehr vorhandene Module: {sorted(stale)}"
