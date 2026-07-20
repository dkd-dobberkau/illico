"""Test-Grenzwächter: keine Datei unter tests/ darf Cloud/Tenant-Module
importieren. Gegenstück zu test_import_boundary.py (für Produktivcode). Damit
läuft `pytest tests/` im öffentlichen illico-Repo ohne die Cloud-Module.
"""
import ast
from pathlib import Path

FORBIDDEN = {"illico_tenants", "illico_cloud", "illico_app_cloud", "illico_cloud_compile"}
CORE_TESTS_DIR = Path(__file__).resolve().parent  # tests/


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):  # walk erfasst auch verschachtelte Imports
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_core_tests_do_not_import_cloud():
    offenders = {}
    for path in sorted(CORE_TESTS_DIR.rglob("*.py")):
        leaked = _imports(path) & FORBIDDEN
        if leaked:
            offenders[str(path.relative_to(CORE_TESTS_DIR))] = sorted(leaked)
    assert not offenders, f"tests/core importiert Cloud-Code: {offenders}"
