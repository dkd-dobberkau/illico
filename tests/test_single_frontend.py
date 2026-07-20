from pathlib import Path
from fastapi.testclient import TestClient
import illico_app

HTML = (Path(__file__).parents[1] / "illico_index.html").read_text(encoding="utf-8")


def test_kein_saas_leak():
    low = HTML.lower()
    # `admin`/`login` bewusst NICHT als blanke Tokens aufgenommen: beide könnten in
    # legitimen, unverwandten Begriffen des Single-Frontends auftauchen (z. B.
    # "Administrator-Kontakt", "Login-Feld" o.ä.). Vor der Erweiterung per grep
    # gegen das aktuelle HTML geprüft — aktuell 0 Treffer für beide, aber als
    # bewusst konservative Wahl trotzdem ausgelassen, um künftige False-Positives
    # bei harmlosen Ergänzungen zu vermeiden.
    for verboten in (
        "impersonate", "/api/me", "admin-dashboard", "panel-tenants",
        "tenant", "impersonat", "mandant", "logout",
    ):
        assert verboten not in low, f"SaaS-Leak: '{verboten}' im Single-Frontend"


def test_ruft_kern_und_single_endpoints():
    for ep in ("/api/stats", "/api/articles", "/api/chat", "/api/ingest", "/api/compile"):
        assert ep in HTML, f"Endpoint {ep} fehlt im Frontend"


def test_core_serves_single_frontend():
    # Frisch über das Modul zugreifen — nicht `from illico_app import create_app`:
    # andere Tests reloaden illico_app (conftest.single_client), was einen stale
    # `create_app`-Import gegen den reload-erneuerten `_DEFAULT_MGMT`-Sentinel
    # miscompare lassen würde (Identitätsvergleich, siehe illico_app._DEFAULT_MGMT).
    app = illico_app.create_app()
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()
