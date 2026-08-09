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


def test_ziel_im_datenverzeichnis_mit_unterschiedlicher_schreibweise(tmp_path: Path):
    """Ziel wird abgelehnt, wenn es ueber eine andere Schreibweise auf dasselbe
    Verzeichnis verweist wie data.

    Ob das zutreffen kann, haengt vom Dateisystem ab (APFS: ja, ext4: nein) -
    deshalb wird das hier festgestellt statt vom Betriebssystem-Namen
    vermutet: existiert der andersgeschriebene Pfad bereits, nachdem nur die
    Originalschreibweise angelegt wurde, ist das Dateisystem case-insensitiv
    und der Test aussagekraeftig. Andernfalls ist er hier gegenstandslos -
    dann prueft der Gegenstueck-Test unten die tatsaechliche Wahrheit.
    """
    data = tmp_path / "TestData"
    data.mkdir()

    andere_schreibweise = tmp_path / "testdata"
    if not andere_schreibweise.exists():
        pytest.skip("Case-sensitives Dateisystem erkannt")

    ziel = andere_schreibweise / "export.zip"

    with pytest.raises(ValueError):
        illico_export.write_export(data, ziel)


def test_ziel_nicht_im_datenverzeichnis_mit_schreibweise_unterschied(tmp_path: Path):
    """Schreibweise-Unterschied allein macht kein Verzeichnis zu einem Sub-Verzeichnis.

    Auf case-sensitiven Filesystemen (Linux) sind /data/Bestand und /data/bestand
    zwei verschiedene Verzeichnisse. Ein Exportziel in /data/bestand darf nicht
    abgelehnt werden, nur weil das Datenverzeichnis /data/Bestand ist.

    Auf case-insensitiven Filesystemen (APFS) laesst sich dieser Test nicht
    ausfuehren, weil /data/Bestand und /data/bestand auf das gleiche
    Verzeichnis verweisen — in diesem Fall wird der Test uebersprungen.
    """
    data = tmp_path / "Bestand"
    data.mkdir()

    # Versuche, ein Verzeichnis mit anderer Schreibweise anzulegen
    other = tmp_path / "bestand"
    try:
        other.mkdir()
    except FileExistsError:
        # Auf case-insensitiven Filesystemen (APFS): Bestand und bestand
        # sind das gleiche Verzeichnis, also existiert other schon
        pytest.skip("Case-insensitives Dateisystem erkannt")

    # Jetzt haben wir zwei verschiedene Verzeichnisse auf case-sensitiven Systemen
    backup = other / "backup"
    backup.mkdir()
    ziel = backup / "export.zip"

    # Das Ziel liegt NICHT im Datenverzeichnis, deshalb sollte es NICHT abgelehnt werden
    result = illico_export.write_export(data, ziel)
    assert result.path == ziel
    assert result.files == 0  # Leer, aber valid


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


def test_cli_keine_halbfertigen_dateien_bei_fehler_mitten_im_schreiben(tmp_path: Path):
    """Scheitert der Export mitten im Schreiben (z.B. PermissionError bei
    archive.write()), darf keine halbfertige Zieldatei zurückbleiben. Ein
    korruptes Archiv ist schlimmer als gar keines."""
    data = _bestand(tmp_path)
    ziel = tmp_path / "export.zip"

    # Eine Quelldatei unlesbar machen, um PermissionError während archive.write() zu verursachen
    quelldatei = data / "wiki" / "artikel.md"
    quelldatei.chmod(0o000)
    try:
        ergebnis = runner.invoke(illico_export.app,
                                 ["-d", str(data), "-o", str(ziel)])

        assert ergebnis.exit_code == 1, f"Befehl sollte fehlschlagen, war: {ergebnis.output}"
        assert not ziel.exists(), (
            f"Zieldatei sollte nicht existieren nach Fehler mitten im Schreiben, "
            f"aber existiert: {ziel}"
        )
    finally:
        quelldatei.chmod(0o644)
