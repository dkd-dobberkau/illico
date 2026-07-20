"""Single-Management — token-gated Web-Ingest/Compile für den Open-Core-Kern.

Public. Importiert KEINEN Cloud-Code. Betreibt Ingest/Compile/Graph global
gegen das eine `wiki/` (kein Tenant). Job-Runner = eigene, kleine Plumbing
(bewusst nicht mit dem Cloud-Runner geteilt, damit Cloud unangetastet bleibt).
"""

import asyncio
import os
import secrets
import sys
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

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
