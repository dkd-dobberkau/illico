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
import os
import typer
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


def _ziel_in_data(ziel: Path, data: Path) -> bool:
    """Prueft, ob ziel im Datenverzeichnis data liegt.

    Ein Vergleich von Pfad-Zeichenketten muesste raten, ob das Dateisystem
    Gross-/Kleinschreibung unterscheidet — und raet auf einer der beiden
    Zielplattformen zwangslaeufig falsch: Linux (Produktivumgebung laut
    Dockerfile) ist case-sensitiv, APFS (macOS) case-insensitiv aber
    case-preserving. Sogar `Path.resolve()` normalisiert die Schreibweise
    auf APFS nicht auf die tatsaechliche Gross-/Kleinschreibung.

    Deshalb wird stattdessen das Dateisystem selbst befragt. ziel existiert
    beim Aufruf noch nicht, aber sein naechster tatsaechlich existierender
    Vorfahre schon — sein Elternverzeichnis, oder, falls das auch noch
    fehlt, dessen Elternverzeichnis usw. Dessen Identitaet (Geraet + Inode,
    ueber os.path.samefile) wird mit data verglichen: das beantwortet
    "ist das dasselbe Verzeichnis?" unabhaengig von der Schreibweise korrekt,
    weil das Betriebssystem selbst entscheidet statt eine Namensregel.
    """
    vorfahr = ziel.parent
    while not vorfahr.exists():
        eltern = vorfahr.parent
        if eltern == vorfahr:
            return False  # Dateisystem-Wurzel erreicht, kein Treffer
        vorfahr = eltern

    for kandidat in (vorfahr, *vorfahr.parents):
        if os.path.samefile(kandidat, data):
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
    if _ziel_in_data(ziel, data):
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


if __name__ == "__main__":
    app()
