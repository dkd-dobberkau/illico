import ast
from pathlib import Path

CORE_MODULES = [
    "illico_ingest", "illico_compile", "illico_chat", "illico_chat_core",
    "illico_graph", "illico_llm", "illico_canonicalize", "illico_crawl_status",
    "illico_frontmatter", "illico_context", "illico_app", "illico_single",
]
FORBIDDEN = {"illico_tenants", "illico_cloud", "illico_app_cloud", "illico_cloud_compile"}
ROOT = Path(__file__).resolve().parent.parent


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):          # walk erfasst auch verschachtelte Imports
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_core_modules_do_not_import_cloud():
    offenders = {}
    for mod in CORE_MODULES:
        path = ROOT / f"{mod}.py"
        leaked = _imports(path) & FORBIDDEN
        if leaked:
            offenders[mod] = sorted(leaked)
    assert not offenders, f"Kern-Module importieren Cloud-Code: {offenders}"
