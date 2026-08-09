# Bestands-Export als ZIP — Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Illico-Datenverzeichnis verlustfrei als ZIP exportieren — über CLI, API und einen Knopf im Web-Interface.

**Architecture:** Ein neues Modul `illico_export.py` enthält die Kernfunktion `write_export(data, ziel, chats)` und die Typer-CLI. Die API-Route in `illico_single.py` schreibt in eine Temp-Datei und liefert sie als `FileResponse` aus, die nach dem Senden per `BackgroundTask` gelöscht wird. Das Frontend lädt über `fetch` + Blob, weil ein einfacher Link den Authorization-Header nicht mitschickt.

**Tech Stack:** Python 3.13, Standardbibliothek (`zipfile`, `tempfile`, `shutil`), Typer, FastAPI, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-09-bestands-export-design.md`
- Deutsche Bezeichner und Kommentare in neuem Code; Commit-Messages deutsch, Umlaute darin als `ae`/`oe`/`ue` umschrieben (Konvention der letzten Commits)
- Quelltext-Kommentare erklären das **Warum**, nicht das Was
- Keine neuen Laufzeit-Abhängigkeiten — ausschließlich Standardbibliothek plus das bereits vorhandene Typer/FastAPI
- Jedes neue Top-Level-Modul muss in `pyproject.toml` unter `only-include` eingetragen werden, sonst fehlt es im Wheel
- Die Route gehört in `single_management_router` (token-geschützt, im Cloud-Overlay nicht eingehängt) — **nicht** in die Kern-Routen von `illico_app.py`
- Testlauf: `.venv-pub/bin/python -m pytest tests/ -q`, muss durchgehend grün bleiben
- Kein `import illico_app` auf Modulebene in `illico_single.py` — nur lokal in Funktionen (Import-Zyklus, siehe Modulkopf dort)

## Dateien

| Datei | Verantwortung |
|---|---|
| `illico_export.py` (neu) | Kernfunktion + Typer-CLI. Einzige Stelle, die weiß, was ins Archiv gehört |
| `tests/test_export.py` (neu) | Tests für Kernfunktion und CLI |
| `illico_single.py` (ändern) | Route `GET /api/export` |
| `tests/test_single_management.py` (ändern) | Tests für die Route |
| `illico_index.html` (ändern) | Knopf, Haken, `doExport()` |
| `tests/test_single_frontend.py` (ändern) | Endpunkt-Präsenz im Frontend |
| `pyproject.toml` (ändern) | `only-include` + `[project.scripts]` |
| `README.md`, `README.en.md` (ändern) | Bedienung und Rückweg per `unzip` |

---

### Task 1: Kernfunktion `write_export`

**Files:**
- Create: `illico_export.py`
- Create: `tests/test_export.py`
- Modify: `pyproject.toml` (Abschnitt `[tool.hatch.build.targets.wheel]`, `only-include`)

**Interfaces:**
- Consumes: nichts
- Produces:
  - `ARCHIVE_ROOT: str = "illico-data"`
  - `@dataclass ExportResult(files: int = 0, bytes_raw: int = 0, path: Path | None = None)`
  - `write_export(data: Path, ziel: Path, chats: bool = True) -> ExportResult`
  - `default_filename(now: datetime | None = None) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_export.py`:

```python
"""Bestands-Export: was ins Archiv gehoert und was nicht."""
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

import illico_export


def _bestand(root: Path) -> Path:
    """Ein Datenverzeichnis mit allem, was ein echter Bestand hat."""
    data = root / "illico-data"
    (data / "raw" / "dkd").mkdir(parents=True)
    (data / "raw" / "dkd" / "s001.md").write_text("Seite", encoding="utf-8")
    (data / "wiki").mkdir()
    (data / "wiki" / "artikel.md").write_text("Artikel", encoding="utf-8")
    (data / "distill" / "v1").mkdir(parents=True)
    (data / "distill" / "v1" / "abc.json").write_text("{}", encoding="utf-8")
    (data / "chats" / "single").mkdir(parents=True)
    (data / "chats" / "single" / "c1.json").write_text("{}", encoding="utf-8")
    (data / "_inventory.json").write_text("{}", encoding="utf-8")
    (data / "_inventory.json.tmp").write_text("halb", encoding="utf-8")
    (data / ".DS_Store").write_text("mac", encoding="utf-8")
    return data


def _namen(archiv: Path) -> set[str]:
    with zipfile.ZipFile(archiv) as z:
        return set(z.namelist())


def test_archiv_enthaelt_den_ganzen_bestand(tmp_path: Path):
    data = _bestand(tmp_path)
    ziel = tmp_path / "export.zip"

    result = illico_export.write_export(data, ziel)

    namen = _namen(ziel)
    assert "illico-data/raw/dkd/s001.md" in namen
    assert "illico-data/wiki/artikel.md" in namen
    assert "illico-data/distill/v1/abc.json" in namen, (
        "die Destillate sind bezahlte Modellaufrufe und muessen mit"
    )
    assert "illico-data/_inventory.json" in namen, (
        "ohne den Cluster-Zustand baut der naechste Compile-Lauf alles neu"
    )
    assert result.files == len(namen)
    assert result.bytes_raw > 0


def test_wegwerf_dateien_bleiben_draussen(tmp_path: Path):
    data = _bestand(tmp_path)
    ziel = tmp_path / "export.zip"

    illico_export.write_export(data, ziel)

    namen = _namen(ziel)
    assert not [n for n in namen if n.endswith(".tmp")], (
        "halb geschriebene Manifeste gehoeren in kein Backup"
    )
    assert not [n for n in namen if n.endswith(".DS_Store")]


def test_chats_sind_default_dabei(tmp_path: Path):
    data = _bestand(tmp_path)
    ziel = tmp_path / "export.zip"

    illico_export.write_export(data, ziel)

    assert "illico-data/chats/single/c1.json" in _namen(ziel)


def test_no_chats_laesst_nur_die_chats_weg(tmp_path: Path):
    data = _bestand(tmp_path)
    ziel = tmp_path / "export.zip"

    illico_export.write_export(data, ziel, chats=False)

    namen = _namen(ziel)
    assert not [n for n in namen if "/chats/" in n]
    assert "illico-data/raw/dkd/s001.md" in namen, "der Rest muss unangetastet bleiben"
    assert "illico-data/_inventory.json" in namen


def test_fehlendes_datenverzeichnis_wirft(tmp_path: Path):
    """Ein leeres Archiv waere schlimmer als ein Fehler: es sieht aus wie ein
    Backup und ist keines."""
    with pytest.raises(FileNotFoundError):
        illico_export.write_export(tmp_path / "gibt-es-nicht", tmp_path / "x.zip")


def test_leeres_datenverzeichnis_ergibt_gueltiges_archiv(tmp_path: Path):
    data = tmp_path / "leer"
    data.mkdir()
    ziel = tmp_path / "export.zip"

    result = illico_export.write_export(data, ziel)

    assert result.files == 0
    assert zipfile.is_zipfile(ziel)


def test_ziel_im_datenverzeichnis_wird_abgelehnt(tmp_path: Path):
    """Sonst packt das Archiv sich selbst ein — je nach Timing waechst es
    unbegrenzt oder enthaelt einen Torso seiner selbst."""
    data = _bestand(tmp_path)
    with pytest.raises(ValueError):
        illico_export.write_export(data, data / "backup.zip")


def test_default_filename_traegt_datum_und_uhrzeit():
    name = illico_export.default_filename(datetime(2026, 8, 9, 16, 12))
    assert name == "illico-export-20260809-1612.zip", (
        "die Uhrzeit muss mit rein, sonst kollidieren zwei Sicherungen am selben Tag"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-pub/bin/python -m pytest tests/test_export.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'illico_export'`

- [ ] **Step 3: Write the module**

Create `illico_export.py`:

```python
"""Bestands-Export: das Datenverzeichnis als ZIP.

Ein Illico-Bestand ist mehr als raw/ und wiki/. Unter distill/ liegt je Seite
ein bezahlter Modellaufruf, und _documents.json bzw. _inventory.json halten
fest, was schon extrahiert und geclustert ist. Ein Archiv ohne sie sieht
vollstaendig aus und zwingt die Zielmaschine trotzdem, den ganzen Bestand neu
zu bauen. Deshalb wird alles mitgenommen, was unter dem Datenverzeichnis
liegt, statt einer Liste erlaubter Ordner: eine Whitelist liesse wiki-de/,
distill-de/ und kuenftige Verzeichnisse still liegen.
"""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import zipfile

ARCHIVE_ROOT = "illico-data"

# Wegwerf-Dateien. `.tmp` entsteht im atomaren Schreibmuster (temp + os.replace)
# und ist per Definition ein halber Zustand.
EXCLUDED_NAMES = {".DS_Store"}
EXCLUDED_SUFFIXES = {".tmp"}


@dataclass
class ExportResult:
    files: int = 0
    bytes_raw: int = 0
    path: Path | None = None


def default_filename(now: datetime | None = None) -> str:
    """Zeitstempel bis zur Minute — zwei Sicherungen am selben Tag duerfen sich
    nicht gegenseitig ueberschreiben."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"illico-export-{stamp}.zip"


def _ausgeschlossen(path: Path, data: Path, chats: bool) -> bool:
    if path.name in EXCLUDED_NAMES or path.suffix in EXCLUDED_SUFFIXES:
        return True
    if not chats:
        teile = path.relative_to(data).parts
        if teile and teile[0] == "chats":
            return True
    return False


def write_export(data: Path, ziel: Path, chats: bool = True) -> ExportResult:
    """Schreibt das Datenverzeichnis als ZIP nach `ziel`.

    Im Archiv liegt alles unter `illico-data/`, damit ein `unzip` nicht das
    Arbeitsverzeichnis vollschuettet und das Ergebnis direkt einsatzfaehig ist.
    """
    data = Path(data)
    ziel = Path(ziel)
    if not data.is_dir():
        raise FileNotFoundError(f"Datenverzeichnis nicht gefunden: {data}")
    if ziel.resolve().is_relative_to(data.resolve()):
        raise ValueError(
            f"Das Ziel {ziel} liegt im Datenverzeichnis — das Archiv wuerde sich "
            "selbst einpacken."
        )

    ziel.parent.mkdir(parents=True, exist_ok=True)
    result = ExportResult(path=ziel)
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(data.rglob("*")):
            if not path.is_file() or _ausgeschlossen(path, data, chats):
                continue
            archive.write(path, f"{ARCHIVE_ROOT}/{path.relative_to(data)}")
            result.files += 1
            result.bytes_raw += path.stat().st_size
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-pub/bin/python -m pytest tests/test_export.py -q`
Expected: 8 passed

- [ ] **Step 5: Register the module for packaging**

In `pyproject.toml`, Abschnitt `[tool.hatch.build.targets.wheel]`, `only-include`: `"illico_export.py"` an die Liste anhängen (hinter `"illico_documents.py",`).

Ohne diesen Eintrag fehlt das Modul im Wheel. In der 0.3er-Reihe fiel genau so ein Packaging-Fehler erst beim Installieren auf, nicht in der Testsuite.

- [ ] **Step 6: Run the full suite**

Run: `.venv-pub/bin/python -m pytest tests/ -q`
Expected: alle grün

- [ ] **Step 7: Commit**

```bash
git add illico_export.py tests/test_export.py pyproject.toml
git commit -m "feat(export): Kernfunktion fuer den Bestands-Export als ZIP"
```

---

### Task 2: CLI `illico-export`

**Files:**
- Modify: `illico_export.py` (Typer-App ans Modulende)
- Modify: `tests/test_export.py` (CLI-Tests anhängen)
- Modify: `pyproject.toml` (Abschnitt `[project.scripts]`)

**Interfaces:**
- Consumes: `write_export`, `default_filename`, `ExportResult` aus Task 1
- Produces: `app` (Typer-Instanz) — Einstiegspunkt `illico-export = "illico_export:app"`

- [ ] **Step 1: Write the failing tests**

An `tests/test_export.py` anhängen:

```python
from typer.testing import CliRunner

runner = CliRunner()


def test_cli_schreibt_an_den_angegebenen_pfad(tmp_path: Path):
    data = _bestand(tmp_path)
    ziel = tmp_path / "mein-backup.zip"

    ergebnis = runner.invoke(illico_export.app,
                             ["-d", str(data), "-o", str(ziel)])

    assert ergebnis.exit_code == 0, ergebnis.output
    assert ziel.exists()
    assert "illico-data/wiki/artikel.md" in _namen(ziel)


def test_cli_ohne_o_erzeugt_zeitstempel_datei(tmp_path: Path, monkeypatch):
    data = _bestand(tmp_path)
    arbeitsverzeichnis = tmp_path / "cwd"
    arbeitsverzeichnis.mkdir()
    monkeypatch.chdir(arbeitsverzeichnis)

    ergebnis = runner.invoke(illico_export.app, ["-d", str(data)])

    assert ergebnis.exit_code == 0, ergebnis.output
    erzeugt = list(arbeitsverzeichnis.glob("illico-export-*.zip"))
    assert len(erzeugt) == 1, f"erwartet genau ein Archiv, war: {erzeugt}"


def test_cli_ueberschreibt_bestehende_datei_nicht(tmp_path: Path):
    """Ein versehentlich ueberschriebenes Backup ist genau der Verlust, den die
    Funktion verhindern soll."""
    data = _bestand(tmp_path)
    ziel = tmp_path / "vorhanden.zip"
    ziel.write_text("altes backup", encoding="utf-8")

    ergebnis = runner.invoke(illico_export.app,
                             ["-d", str(data), "-o", str(ziel)])

    assert ergebnis.exit_code == 1
    assert ziel.read_text(encoding="utf-8") == "altes backup"


def test_cli_no_chats_wird_durchgereicht(tmp_path: Path):
    data = _bestand(tmp_path)
    ziel = tmp_path / "ohne-chats.zip"

    ergebnis = runner.invoke(illico_export.app,
                             ["-d", str(data), "-o", str(ziel), "--no-chats"])

    assert ergebnis.exit_code == 0, ergebnis.output
    assert not [n for n in _namen(ziel) if "/chats/" in n]


def test_cli_meldet_fehlendes_datenverzeichnis(tmp_path: Path):
    ergebnis = runner.invoke(illico_export.app,
                             ["-d", str(tmp_path / "weg"), "-o", str(tmp_path / "x.zip")])

    assert ergebnis.exit_code == 1
    assert not (tmp_path / "x.zip").exists()


def test_cli_meldet_nicht_schreibbares_ziel(tmp_path: Path):
    """Ein Stacktrace ist keine Fehlermeldung — wer ein Backup anstoesst, soll
    lesen koennen, warum es nicht ging."""
    data = _bestand(tmp_path)
    gesperrt = tmp_path / "gesperrt"
    gesperrt.mkdir()
    gesperrt.chmod(0o500)
    try:
        ergebnis = runner.invoke(illico_export.app,
                                 ["-d", str(data), "-o", str(gesperrt / "x.zip")])
        assert ergebnis.exit_code == 1
        assert "✗" in ergebnis.output or "✗" in (ergebnis.stderr or "")
    finally:
        gesperrt.chmod(0o700)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-pub/bin/python -m pytest tests/test_export.py -q -k cli`
Expected: FAIL mit `AttributeError: module 'illico_export' has no attribute 'app'`

- [ ] **Step 3: Add the CLI**

Am Ende von `illico_export.py` anhängen (und `import os` sowie `import typer` oben ergänzen):

```python
app = typer.Typer(add_completion=False,
                  help="Exportiert das Illico-Datenverzeichnis als ZIP.")


@app.command()
def export(
    output: Path = typer.Option(
        None, "--output", "-o",
        help="Zieldatei. Ohne Angabe: illico-export-<zeitstempel>.zip im "
             "aktuellen Verzeichnis"),
    data: Path = typer.Option(
        Path(os.environ.get("ILLICO_DATA", "./illico-data")), "--data", "-d",
        help="Illico-Datenverzeichnis"),
    no_chats: bool = typer.Option(
        False, "--no-chats", help="Chatverlaeufe auslassen"),
):
    """Packt das Datenverzeichnis in ein ZIP — inklusive Destillaten und
    Manifesten, damit der Bestand woanders ohne neue Modellkosten weiterlaeuft.
    """
    ziel = Path(output) if output else Path.cwd() / default_filename()
    if ziel.exists():
        typer.echo(f"✗ {ziel} existiert bereits — nicht ueberschrieben.", err=True)
        raise typer.Exit(1)
    try:
        result = write_export(data, ziel, chats=not no_chats)
    except (OSError, ValueError) as exc:
        # OSError deckt fehlendes Datenverzeichnis (FileNotFoundError) und
        # nicht schreibbares Ziel (PermissionError) ab. Ein Stacktrace waere
        # hier keine Fehlermeldung.
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(1)
    groesse = ziel.stat().st_size / 1_048_576
    typer.echo(f"✓ {result.files} Dateien in {ziel} ({groesse:.1f} MB)")
    if no_chats:
        typer.echo("  Chatverlaeufe ausgelassen.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-pub/bin/python -m pytest tests/test_export.py -q`
Expected: 14 passed (8 aus Task 1, 6 aus dieser Task)

- [ ] **Step 5: Register the console script**

In `pyproject.toml`, Abschnitt `[project.scripts]`, hinter `illico-serve` einfügen:

```toml
illico-export = "illico_export:app"
```

- [ ] **Step 6: Verify the installed command**

Run: `.venv-pub/bin/pip install -e . -q && .venv-pub/bin/illico-export --help`
Expected: Hilfetext mit `--output`, `--data`, `--no-chats`

- [ ] **Step 7: Run the full suite and commit**

```bash
.venv-pub/bin/python -m pytest tests/ -q
git add illico_export.py tests/test_export.py pyproject.toml
git commit -m "feat(export): CLI-Befehl illico-export"
```

---

### Task 3: API-Route `GET /api/export`

**Files:**
- Modify: `illico_single.py` (Imports oben, Route ans Router-Ende)
- Modify: `tests/test_single_management.py`

**Interfaces:**
- Consumes: `illico_export.write_export`, `illico_export.default_filename`; `jobs`-Dict und `single_management_router` aus `illico_single.py`
- Produces: Route `GET /api/export?chats=<bool>` → `application/zip`, optionaler Header `X-Illico-Warning`

- [ ] **Step 1: Write the failing tests**

An `tests/test_single_management.py` anhängen:

```python
import zipfile
import io


def _bestand_anlegen(data):
    (data / "raw").mkdir(parents=True, exist_ok=True)
    (data / "raw" / "s1.md").write_text("Seite", encoding="utf-8")
    (data / "chats" / "single").mkdir(parents=True, exist_ok=True)
    (data / "chats" / "single" / "c1.json").write_text("{}", encoding="utf-8")


def test_export_liefert_ein_zip(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    _bestand_anlegen(tmp_path)

    r = client.get("/api/export")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "illico-export-" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert "illico-data/raw/s1.md" in z.namelist()


def test_export_ohne_chats(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    _bestand_anlegen(tmp_path)

    r = client.get("/api/export?chats=false")

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert not [n for n in z.namelist() if "/chats/" in n]


def test_export_raeumt_die_temp_datei_ab(client, tmp_path, monkeypatch):
    """Sonst fuellt jeder Download die Platte des Servers."""
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    _bestand_anlegen(tmp_path)
    gemerkt = []
    echtes_mkdtemp = illico_single.tempfile.mkdtemp

    def merkend(*a, **kw):
        pfad = echtes_mkdtemp(*a, **kw)
        gemerkt.append(Path(pfad))
        return pfad

    monkeypatch.setattr(illico_single.tempfile, "mkdtemp", merkend)

    r = client.get("/api/export")

    assert r.status_code == 200
    assert gemerkt, "die Route muss ein Temp-Verzeichnis angelegt haben"
    assert not gemerkt[0].exists(), "das Temp-Verzeichnis muss nach dem Senden weg sein"


def test_export_warnt_bei_laufendem_job(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    _bestand_anlegen(tmp_path)
    illico_single.jobs.clear()
    illico_single._new_job("compile-1", "compile")

    r = client.get("/api/export")

    illico_single.jobs.clear()
    assert "x-illico-warning" in r.headers
    assert "compile" in r.headers["x-illico-warning"]


def test_export_ohne_job_ohne_warnung(client, tmp_path, monkeypatch):
    """Eine Dauerwarnung wird ueberlesen."""
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    _bestand_anlegen(tmp_path)
    illico_single.jobs.clear()

    r = client.get("/api/export")

    assert "x-illico-warning" not in r.headers


def test_export_ohne_datenverzeichnis_404(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ILLICO_SINGLE_TOKEN", raising=False)
    monkeypatch.setattr("illico_app.DATA_DIR", tmp_path / "gibt-es-nicht")

    r = client.get("/api/export")

    assert r.status_code == 404


def test_export_verlangt_token_wenn_gesetzt(client, monkeypatch):
    monkeypatch.setenv("ILLICO_SINGLE_TOKEN", "geheim")
    r = client.get("/api/export")
    assert r.status_code == 401
```

Ergänze oben in der Datei den Import `from pathlib import Path`, falls noch nicht vorhanden.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-pub/bin/python -m pytest tests/test_single_management.py -q -k export`
Expected: FAIL mit 404 (Route existiert nicht)

- [ ] **Step 3: Add the route**

In `illico_single.py` die Imports oben ergänzen:

```python
import shutil
import tempfile
from pathlib import Path

from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
```

Dann ans Ende des Routers anhängen:

```python
@single_management_router.get("/api/export")
def api_export(chats: bool = True):
    """Liefert das komplette Datenverzeichnis als ZIP.

    Ueber eine Temp-Datei statt aus dem Speicher: der Bedarf bleibt damit
    konstant, egal wie gross der Bestand geworden ist.
    """
    import illico_app  # lazy: bricht Import-Zyklus (siehe Modulkopf)
    import illico_export

    data = illico_app.DATA_DIR
    if not data.is_dir():
        raise HTTPException(404, "Kein Datenverzeichnis")

    verzeichnis = Path(tempfile.mkdtemp(prefix="illico-export-"))
    ziel = verzeichnis / illico_export.default_filename()
    illico_export.write_export(data, ziel, chats=chats)

    headers = {}
    laufend = [f"{j['type']} ({jid})" for jid, j in jobs.items()
               if j.get("status") == "running"]
    if laufend:
        # Der Rumpf ist ein ZIP-Datenstrom und kann keinen Hinweis tragen; das
        # Frontend liest diesen Header aus. Nur setzen, wenn wirklich ein Job
        # laeuft — eine Dauerwarnung wird ueberlesen.
        headers["X-Illico-Warning"] = (
            "Laufender Job: " + ", ".join(laufend)
            + " — das Archiv ist moeglicherweise kein konsistenter Snapshot."
        )

    return FileResponse(
        ziel, media_type="application/zip", filename=ziel.name, headers=headers,
        background=BackgroundTask(shutil.rmtree, verzeichnis, ignore_errors=True),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-pub/bin/python -m pytest tests/test_single_management.py -q`
Expected: alle grün

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv-pub/bin/python -m pytest tests/ -q
git add illico_single.py tests/test_single_management.py
git commit -m "feat(export): Route GET /api/export im Management-Router"
```

---

### Task 4: Knopf im Web-Interface

**Files:**
- Modify: `illico_index.html` (Markup bei Zeile ~578, Funktion nach `doGraphRebuild`)
- Modify: `tests/test_single_frontend.py`

**Interfaces:**
- Consumes: Route `GET /api/export?chats=<bool>` aus Task 3; die vorhandenen Frontend-Helfer `authHeaders(obj)` und `showToast(text)`
- Produces: nichts für spätere Tasks

- [ ] **Step 1: Write the failing test**

In `tests/test_single_frontend.py` die bestehende Endpunkt-Liste in `test_ruft_kern_und_single_endpoints` um `/api/export` erweitern und diesen Test anhängen:

```python
def test_export_laedt_per_blob_nicht_per_link():
    """Ein <a href> schickt den Authorization-Header nicht mit. Bei gesetztem
    ILLICO_SINGLE_TOKEN liefe der Download sonst in ein 401, das der Nutzer
    nur als kaputte Datei sieht."""
    assert "/api/export" in HTML
    assert "createObjectURL" in HTML
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-pub/bin/python -m pytest tests/test_single_frontend.py -q`
Expected: FAIL — `/api/export` fehlt im HTML

- [ ] **Step 3: Add markup**

In `illico_index.html` nach der Zeile mit `onclick="doGraphRebuild()"` (Zeile ~578) einfügen:

```html
<button class="action-btn" onclick="doExport()">⤓ Bestand exportieren</button>
<label style="display:block;margin-top:6px;font-size:0.85em;">
  <input type="checkbox" id="export-chats" checked> Chatverläufe einschließen
</label>
```

- [ ] **Step 4: Add the function**

Nach `doGraphRebuild()` einfügen:

```javascript
async function doExport() {
  const chats = document.getElementById('export-chats').checked;
  try {
    // fetch + Blob statt <a href>: nur so geht der Authorization-Header mit,
    // sonst endet der Download bei gesetztem Token in einem 401.
    const r = await fetch('/api/export?chats=' + (chats ? 'true' : 'false'),
                          { headers: authHeaders({}) });
    if (!r.ok) { showToast(r.status === 401 ? 'Token erforderlich' : 'Export fehlgeschlagen'); return; }
    const warnung = r.headers.get('X-Illico-Warning');
    const blob = await r.blob();
    const name = (r.headers.get('Content-Disposition') || '').match(/filename="?([^";]+)"?/)?.[1]
                 || 'illico-export.zip';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    showToast(warnung ? 'Export geladen — ' + warnung : 'Export geladen');
  } catch (e) { showToast('Export fehlgeschlagen'); }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv-pub/bin/python -m pytest tests/test_single_frontend.py -q`
Expected: alle grün

- [ ] **Step 6: Try it in the running app**

```bash
.venv-pub/bin/illico-serve --port 8000
```

Im Browser `http://localhost:8000` öffnen, den Reiter „Verwalten" aufsuchen, auf „Bestand exportieren" klicken. Erwartet: eine Datei `illico-export-<zeitstempel>.zip` landet im Download-Ordner und lässt sich entpacken. Danach den Server beenden.

- [ ] **Step 7: Run the full suite and commit**

```bash
.venv-pub/bin/python -m pytest tests/ -q
git add illico_index.html tests/test_single_frontend.py
git commit -m "feat(export): Knopf Bestand exportieren im Web-Interface"
```

---

### Task 5: Dokumentation

**Files:**
- Modify: `README.md`
- Modify: `README.en.md`

**Interfaces:**
- Consumes: alles aus Tasks 1–4
- Produces: nichts

- [ ] **Step 1: Add the German section**

In `README.md` nach dem Abschnitt zum Dokument-Ingest einfügen:

```markdown
### Bestand exportieren

```bash
illico-export -o backup.zip          # oder ohne -o: illico-export-<zeitstempel>.zip
illico-export -o backup.zip --no-chats
```

Im Web-Interface gibt es dafür unter „Verwalten" den Knopf **Bestand
exportieren**.

Das Archiv enthält das komplette Datenverzeichnis: `raw/`, `wiki/`, die
Destillate unter `distill/`, den Graphen und die Manifeste. Das ist Absicht —
jedes Destillat ist ein bezahlter Modellaufruf, und ohne `_documents.json` und
`_inventory.json` baut der nächste Compile-Lauf alles neu. Chatverläufe sind
standardmäßig dabei; `--no-chats` lässt sie weg, wenn das Archiv weitergegeben
werden soll.

Zurückspielen geht ohne eigenen Befehl — das Archiv entpackt sich 1:1:

```bash
unzip backup.zip -d /ziel/verzeichnis
```

Ein Export während eines laufenden `ingest` oder `compile` ist kein
konsistenter Snapshot. Das Web-Interface warnt in dem Fall; die CLI läuft in
einem eigenen Prozess und kennt die Jobs des Servers nicht.
```

- [ ] **Step 2: Add the English section**

In `README.en.md` an der entsprechenden Stelle:

```markdown
### Exporting a collection

```bash
illico-export -o backup.zip          # or without -o: illico-export-<timestamp>.zip
illico-export -o backup.zip --no-chats
```

The web interface has a **Bestand exportieren** button under "Verwalten".

The archive holds the entire data directory: `raw/`, `wiki/`, the distillates
under `distill/`, the graph and the manifests. That is deliberate — every
distillate is a paid model call, and without `_documents.json` and
`_inventory.json` the next compile run rebuilds everything. Chat histories are
included by default; `--no-chats` leaves them out when the archive is meant to
be handed on.

Restoring needs no separate command — the archive unpacks 1:1:

```bash
unzip backup.zip -d /target/directory
```

An export taken while `ingest` or `compile` is running is not a consistent
snapshot. The web interface warns about this; the CLI runs in its own process
and does not know about the server's jobs.
```

- [ ] **Step 3: Verify the commands in the docs actually work**

```bash
cd /tmp && rm -f probe.zip
/Users/olivier/Versioncontrol/local/illico-pub/.venv-pub/bin/illico-export \
  -d /Users/olivier/Versioncontrol/local/illico-pub/illico-data -o /tmp/probe.zip
unzip -l /tmp/probe.zip | head -5
rm -f probe.zip
```

Expected: Archiv wird geschrieben, Inhalt beginnt mit `illico-data/`

- [ ] **Step 4: Commit**

```bash
git add README.md README.en.md
git commit -m "docs(export): Bestands-Export in beiden READMEs"
```

---

## Abschluss

Nach Task 5:

- [ ] `.venv-pub/bin/python -m pytest tests/ -q` — alles grün
- [ ] `illico-export` gegen den echten `illico-data`-Bestand laufen lassen und das Archiv entpacken; prüfen, dass `distill/`, `_inventory.json` und `_documents.json` enthalten sind
- [ ] Prüfen, dass ein aus dem Archiv wiederhergestelltes Verzeichnis einen `illico-compile`-Lauf ohne neue Destillation übersteht (Phase 1 muss „keine Aenderungen" bzw. volle Destillat-Zahl aus dem Cache melden) — das ist der eigentliche Zweck des Features und der einzige Test, der ihn wirklich prüft
