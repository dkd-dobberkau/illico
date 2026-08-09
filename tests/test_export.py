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
