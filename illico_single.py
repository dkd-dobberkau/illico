"""Single-Management — token-gated Web-Ingest/Compile für den Open-Core-Kern.

Public. Importiert KEINEN Cloud-Code. Betreibt Ingest/Compile/Graph global
gegen das eine `wiki/` (kein Tenant). Job-Runner = eigene, kleine Plumbing
(bewusst nicht mit dem Cloud-Runner geteilt, damit Cloud unangetastet bleibt).
"""

import asyncio
import os
import secrets
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import illico_llm

# HINWEIS: `import illico_app` NICHT auf Modul-Ebene — es entstünde ein Import-
# Zyklus (illico_app.create_app() importiert dieses Modul lazy). Beim Start über
# `python3 illico_app.py` läuft illico_app als __main__ UND würde hier ein
# zweites Mal als Modul geladen, mitten im ersten Import → ImportError. Deshalb
# wird illico_app in jeder Funktion lokal importiert.


# ─── Token-Gate ───────────────────────────────────────────────────────────────

def require_single_token(authorization: str | None = Header(default=None)) -> None:
    """Optionaler Zugangs-Token. Leer → offen (localhost-Default); gesetzt →
    verlangt `Authorization: Bearer <token>`."""
    expected = os.environ.get("ILLICO_SINGLE_TOKEN", "")
    if not expected:
        return
    # Konstante Laufzeit gegen Timing-Angriffe: erst auf None prüfen (compare_digest
    # verlangt zwei str/bytes gleichen Typs), dann secrets.compare_digest statt `!=`.
    if authorization is None or not secrets.compare_digest(authorization, f"Bearer {expected}"):
        raise HTTPException(401, "Ungültiger oder fehlender Token")


# ─── Job-Runner ───────────────────────────────────────────────────────────────

jobs: dict[str, dict] = {}


async def _run_job(job_id: str, argv: list[str]) -> None:
    """Startet den Subprozess und streamt stdout zeilenweise in jobs[job_id]."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        jobs[job_id]["output"] += line.decode(errors="replace")
    await proc.wait()
    jobs[job_id]["status"] = "done" if proc.returncode == 0 else "error"
    jobs[job_id]["finished"] = datetime.now().isoformat()


def _new_job(job_id: str, jtype: str, **extra) -> None:
    jobs[job_id] = {
        "type": jtype, "status": "running", "output": "",
        "started": datetime.now().isoformat(), "finished": None, **extra,
    }


# rel-Pfad → Domain über alle raw/-Dateien: Kern-Helfer wiederverwenden (DRY,
# global, kein Tenant-Filter). NICHT reimplementieren — `illico_app._raw_domain_map`
# liest die Frontmatter-Domain bereits korrekt aus dem Datei-Inhalt.


# ─── Request-Modelle ──────────────────────────────────────────────────────────

class SingleIngestRequest(BaseModel):
    url: str
    depth: int = 2


class SingleCompileRequest(BaseModel):
    lint_only: bool = False
    lang: str | None = None


class SingleGraphRequest(BaseModel):
    lang: str | None = None


# ─── Router ───────────────────────────────────────────────────────────────────

single_management_router = APIRouter(dependencies=[Depends(require_single_token)])


@single_management_router.post("/api/ingest")
async def api_ingest(req: SingleIngestRequest):
    import illico_app  # lazy: bricht Import-Zyklus (siehe Modulkopf)
    job_id = f"ingest-{int(datetime.now().timestamp())}"
    _new_job(job_id, "ingest", url=req.url)
    argv = [sys.executable, "-m", "illico_ingest", "ingest", req.url,
            "--depth", str(req.depth), "--data", str(illico_app.DATA_DIR)]
    asyncio.create_task(_run_job(job_id, argv))
    return {"status": "started", "job_id": job_id, "url": req.url}


@single_management_router.post("/api/compile")
async def api_compile(req: SingleCompileRequest):
    import illico_app  # lazy: bricht Import-Zyklus (siehe Modulkopf)
    job_id = f"compile-{int(datetime.now().timestamp())}"
    _new_job(job_id, "compile")
    argv = [sys.executable, "-m", "illico_compile", "--data", str(illico_app.DATA_DIR),
            "--model", illico_llm.ANSWER_MODEL]
    if req.lint_only:
        argv.append("--lint")
    if req.lang:
        argv += ["--lang", req.lang]
    asyncio.create_task(_run_job(job_id, argv))
    return {"status": "started", "job_id": job_id}


@single_management_router.post("/api/graph/rebuild")
async def api_graph_rebuild(req: SingleGraphRequest):
    import illico_app  # lazy: bricht Import-Zyklus (siehe Modulkopf)
    job_id = f"graph-{int(datetime.now().timestamp())}"
    _new_job(job_id, "graph", lang=req.lang or "")
    argv = [sys.executable, "-m", "illico_compile", "--data", str(illico_app.DATA_DIR),
            "--model", illico_llm.ANSWER_MODEL, "--graph-only"]
    if req.lang:
        argv += ["--lang", req.lang]
    asyncio.create_task(_run_job(job_id, argv))
    return {"status": "started", "job_id": job_id, "lang": req.lang or ""}


@single_management_router.delete("/api/raw/{domain}")
def api_delete_raw(domain: str):
    """Löscht alle Raw-Dateien einer Domain (global)."""
    import illico_app  # lazy: bricht Import-Zyklus (siehe Modulkopf)
    raw_dir = illico_app.DATA_DIR / "raw"
    if not raw_dir.exists():
        raise HTTPException(404, "Kein raw/-Verzeichnis")
    raw_domains = illico_app._raw_domain_map()
    to_delete = [rel for rel, d in raw_domains.items() if d == domain]
    if not to_delete:
        raise HTTPException(404, f"Keine Dateien für Domain '{domain}'")
    deleted = 0
    for rel in to_delete:
        path = raw_dir / rel
        if path.exists():
            path.unlink()
            deleted += 1
    for d in sorted(raw_dir.rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return {"domain": domain, "deleted": deleted}


@single_management_router.get("/api/jobs")
def api_jobs():
    return {jid: {k: v for k, v in j.items() if k != "output"} for jid, j in jobs.items()}


@single_management_router.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job nicht gefunden")
    return jobs[job_id]


@single_management_router.get("/api/export")
def api_export(chats: bool = True):
    """Liefert das komplette Datenverzeichnis als ZIP.

    Ueber eine Temp-Datei statt aus dem Speicher: der Speicherbedarf bleibt
    damit konstant, egal wie gross der Bestand geworden ist. Das gilt fuer den
    Erfolgsfall; Fehlerpfade und abgebrochene Downloads sind unten dokumentiert.
    """
    import illico_app  # lazy: bricht Import-Zyklus (siehe Modulkopf)
    import illico_export

    data = illico_app.DATA_DIR
    if not data.is_dir():
        raise HTTPException(404, "Kein Datenverzeichnis")

    # tempfile.mkdtemp() ohne dir= schreibt nach tempfile.gettempdir(); im
    # Produktiv-Image (siehe Dockerfile) ist das die beschreibbare Container-
    # Schicht unter /tmp, kein gemountetes Volume. Ein Leck ueberlebt also
    # keinen Container-Neustart/-Redeploy — das ist eine Rueckfallebene auf
    # Betriebssystem-/Orchestrierungs-Ebene, keine Garantie innerhalb eines
    # lange laufenden Prozesses. Sie deckt insbesondere NICHT den Fall ab, dass
    # ein Client den Download mitten im Stream abbricht: Starlettes
    # BackgroundTask an FileResponse laeuft laut Quelle nur nach vollstaendig
    # gesendetem Response-Body, ein Verbindungsabbruch laesst das
    # Temp-Verzeichnis dann bis zum naechsten Neustart liegen. Ein robusterer
    # Mechanismus (z.B. Abraeumen beim Prozessstart) ist neuer Funktionsumfang
    # und bewusst nicht Teil dieser Route.
    verzeichnis = Path(tempfile.mkdtemp(prefix="illico-export-"))
    ziel = verzeichnis / illico_export.default_filename()
    try:
        illico_export.write_export(data, ziel, chats=chats)
    except (OSError, ValueError) as exc:
        # Gleiche Fehlerklassen wie im CLI-Pfad (illico_export.py::export):
        # OSError deckt unlesbare Quelldateien und volle Platte ab, ValueError
        # den (hier eigentlich unerreichbaren) Fall "Ziel liegt im
        # Datenverzeichnis". Ohne dieses except liefe die Exception ungefangen
        # durch FastAPI zu einem unkontrollierten 500 UND das Temp-Verzeichnis
        # bliebe liegen, weil FileResponse (und damit der BackgroundTask fuers
        # Aufraeumen) nie konstruiert wird. 500 statt 404, damit der Fall nicht
        # mit "Datenverzeichnis fehlt" verwechselt wird.
        shutil.rmtree(verzeichnis, ignore_errors=True)
        raise HTTPException(500, f"Export fehlgeschlagen: {exc}") from exc

    headers = {}
    laufend = [f"{j['type']} ({jid})" for jid, j in jobs.items()
               if j.get("status") == "running"]
    if laufend:
        # Der Rumpf ist ein ZIP-Datenstrom und kann keinen Hinweis tragen; das
        # Frontend liest diesen Header aus. Nur setzen, wenn wirklich ein Job
        # laeuft — eine Dauerwarnung wird ueberlesen.
        # HTTP-Header muessen Latin-1-kodierbar sein (siehe RFC 7230) — deshalb
        # hier ein einfacher Bindestrich statt Halbgeviertstrich, sonst wirft
        # Starlette beim Bauen der Response einen UnicodeEncodeError.
        headers["X-Illico-Warning"] = (
            "Laufender Job: " + ", ".join(laufend)
            + " - das Archiv ist moeglicherweise kein konsistenter Snapshot."
        )

    return FileResponse(
        ziel, media_type="application/zip", filename=ziel.name, headers=headers,
        background=BackgroundTask(shutil.rmtree, verzeichnis, ignore_errors=True),
    )
