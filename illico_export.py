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


def _ziel_in_data(ziel: Path, data: Path) -> bool:
    """Prueft, ob ziel im Datenverzeichnis liegt.

    Nutzt case-sensitiven Vergleich als Primärlogik (funktioniert auf Linux),
    mit case-insensitivem Fallback für APFS (macOS).
    """
    ziel_resolved = ziel.resolve()
    data_resolved = data.resolve()

    # Schritt 1: Case-sensitiver Vergleich (primär, funktioniert auf Linux)
    # is_relative_to() ist die zuverlässigste Methode auf case-sensitiven Systemen
    try:
        ziel_resolved.relative_to(data_resolved)
        return True
    except ValueError:
        pass

    # Schritt 2: Fallback zu case-insensitivem Vergleich für APFS
    # Nur wenn der case-sensitive Vergleich fehlgeschlagen ist.
    # Dies ist defensiv: nur nötig auf Systemen, wo case-insensitivity auftritt.
    ziel_parts = ziel_resolved.parts
    data_parts = data_resolved.parts

    if len(ziel_parts) < len(data_parts):
        return False

    # Vergleiche die ersten len(data_parts) Komponenten case-insensitiv
    for zp, dp in zip(ziel_parts[:len(data_parts)], data_parts):
        if zp.lower() != dp.lower():
            return False

    return True


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
