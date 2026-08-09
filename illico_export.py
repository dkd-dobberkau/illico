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
import tempfile
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

    Der Vorfahren-Lauf arbeitet dafuer auf dem absoluten Pfad: `Path(".").parents`
    ist leer (kein Tippfehler — bei relativen Pfaden liefert pathlib da schlicht
    nichts), ein Lauf ueber `ziel.parent.parents` wuerde also bei relativem ziel
    an der ersten Ebene abbrechen und nie die eigentlichen Vorfahren pruefen.
    `.resolve()` macht daraus zuerst einen absoluten Pfad, dessen `.parents`
    tatsaechlich bis zur Dateisystem-Wurzel reicht — unabhaengig davon, ob
    ziel und data relativ oder absolut uebergeben wurden.
    """
    vorfahr = ziel.resolve().parent
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

    Der Schreibvorgang ist atomar: die ZIP-Datei wird zu einer eindeutig
    benannten Temp-Datei geschrieben und erst bei erfolgreicher Fertigstellung
    zu `ziel` umbenannt. Fehlt der Export mitten im Schreiben (z.B. unlesbare
    Quelldatei, voll Festplatte), bleibt keine halbfertige Zieldatei zurück
    und die Temp-Datei wird entfernt.

    Existiert `ziel` bereits, wird es bedingungslos überschrieben — os.replace
    kennt kein "nicht überschreiben". Der Schutz vor versehentlichem
    Überschreiben (siehe CLI-Befehl `export`) sitzt ausschließlich beim
    Aufrufer; wer diese Funktion direkt aufruft, muss selbst vorher prüfen.
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

    # Schreib zur temp-Datei und ersetze atomar bei Erfolg. Der Name muss
    # unteilbar sein: zwei ueberlappende Laeufe auf dieselbe Zieldatei (z.B.
    # zwei sich ueberschneidende Cron-Jobs mit dem in der README empfohlenen
    # festen Dateinamen) duerften sich sonst dieselbe .tmp-Datei teilen — beide
    # Prozesse schrieben dann verschraenkt in dieselbe Inode, und wer zuerst
    # mit os.replace fertig ist, meldet faelschlich Erfolg fuer ein Archiv, das
    # der andere Prozess gerade zerstoert. tempfile.mkstemp() legt die Datei
    # per O_EXCL atomar und garantiert kollisionsfrei an; dir=ziel.parent haelt
    # sie im selben Verzeichnis wie ziel, sonst waere os.replace kein atomares
    # Rename mehr (Dateisystemgrenze).
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{ziel.name}.", suffix=".tmp", dir=ziel.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(data.rglob("*")):
                if not path.is_file() or _ausgeschlossen(path, data, chats):
                    continue
                archive.write(path, f"{ARCHIVE_ROOT}/{path.relative_to(data)}")
                result.files += 1
                result.bytes_raw += path.stat().st_size
        os.replace(tmp, ziel)
    except BaseException:
        # Ohne dieses Aufraeumen bliebe die Temp-Datei bei jedem Fehlschlag
        # (volle Platte, unlesbare Quelldatei) liegen — unsichtbarer
        # Plattenverbrauch, der sich bei jedem erneuten Versuch summiert.
        tmp.unlink(missing_ok=True)
        raise
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
