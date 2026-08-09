# Bestands-Export als ZIP — Entwurf

**Datum:** 2026-08-09
**Status:** abgenommen, bereit für die Umsetzungsplanung
**Betrifft:** `illico-pub` (Single). Der Cloud-Overlay ist nicht Teil dieser Spec.

## Warum

Ein Illico-Bestand liegt vollständig unter `illico-data/`, aber es gibt keinen
Weg, ihn als Ganzes mitzunehmen. Wer den Bestand auf eine andere Maschine
bringen oder sichern will, muss das Verzeichnis von Hand einpacken und dabei
wissen, was dazugehört.

Das ist teurer, als es aussieht. Unter `distill/` liegt je Seite ein
Destillat, das einen bezahlten Modellaufruf gekostet hat — im Testbestand 450
Stück. Ein Archiv, das nur `raw/` und `wiki/` enthält, sieht vollständig aus
und zwingt die Zielmaschine trotzdem, den gesamten Bestand neu zu destillieren.
Dieselbe Falle gilt für `_documents.json` (welche PDFs schon extrahiert sind)
und `_inventory.json` (der Cluster-Zustand, an dem der Delta-Mechanismus
hängt): fehlen sie, baut der nächste Compile-Lauf alles neu.

Ein Export muss deshalb **verlustfrei** sein, nicht hübsch.

## Entscheidungen

| Frage | Entscheidung | Begründung |
|---|---|---|
| Zweck | Umzug und Sicherung, nicht Weitergabe | Vollständigkeit schlägt Schlankheit; ein halbes Archiv kostet echtes Geld |
| Umfang | Alles unter `illico-data/`, mit Ausnahmeliste | Eine Whitelist ließe `wiki-de/`, `distill-de/` und künftige Verzeichnisse still liegen |
| Chatverläufe | Dabei, per Schalter abwählbar | Ein Umzug braucht sie; wer weitergibt, will sie nicht mitgeben |
| Einstiegspunkte | CLI, API und Web-Knopf | Cron-Sicherung ohne Server *und* Bedienung aus dem Browser |
| Auslieferung im Web | Temp-Datei statt Speicher | Der Speicherbedarf bleibt konstant, unabhängig von der Bestandsgröße |
| Ort der Route | `single_management_router` | Token-geschützt und im Cloud-Overlay nicht eingehängt |
| Archivstruktur | Wurzelordner `illico-data/` | `unzip` schüttet nicht das Arbeitsverzeichnis voll |

### Warum kein Job wie bei ingest und compile

Der Management-Router führt `ingest`, `compile` und `graph/rebuild` als
Hintergrund-Jobs mit Job-ID und Status-Polling. Dieses Muster existiert, weil
die Läufe Minuten dauern und Hunderte Modellaufrufe machen. Ein ZIP über einen
Textbestand ist in Millisekunden fertig und ruft kein Modell auf. Die
Job-Verwaltung wäre hier reine Zeremonie und brächte einen zweiten Zustand,
der schiefgehen kann.

### Warum kein Import-Befehl

Das Archiv entpackt sich 1:1 nach `illico-data/`. Ein Restore-Kommando wäre
Code, der eine `unzip`-Zeile ersetzt, plus ein zweiter Pfad, auf dem
Überschreib-Unfälle passieren können. Der Weg gehört in die README.

## Aufbau

### Kernfunktion

Neues Modul `illico_export.py` mit einer Aufgabe, analog zu
`illico_distill.py` und `illico_documents.py`:

```python
def write_export(data: Path, ziel: Path, chats: bool = True) -> ExportResult
```

Schreibt ein ZIP an `ziel` und meldet zurück, was hineingegangen ist
(Dateizahl und Größe, für die Rückmeldung in CLI und Web).

Alle Einstiegspunkte rufen ausschließlich diese Funktion. Die Web-Route gibt
ihr einen Temp-Pfad, die CLI den Pfad des Nutzers. Damit gibt es genau eine
Stelle, die weiß, was ins Archiv gehört — die Regel steht nicht dreimal leicht
verschieden im Code.

### Einstiegspunkte

| Weg | Aufruf |
|---|---|
| CLI | `illico-export [-o pfad.zip] [-d illico-data] [--no-chats]` |
| API | `GET /api/export?chats=true` → `application/zip` |
| Web | Knopf „Bestand exportieren" mit Haken „Chatverläufe einschließen" |

`-o` ist optional: ohne Angabe schreibt die CLI
`illico-export-YYYYMMDD-HHMM.zip` ins aktuelle Verzeichnis. Eine bestehende
Datei wird nicht überschrieben, sondern führt zu Exit 1 — ein versehentlich
überschriebenes Backup ist genau der Verlust, den die Funktion verhindern
soll.

Die CLI kommt als eigener Konsolen-Einstiegspunkt in `pyproject.toml`, damit
eine Sicherung per Cron ohne laufenden Server möglich ist.

Der Endpunkt gehört in den `single_management_router` und nicht in die
Kern-Routen. Er liefert den kompletten Datenbestand aus; im
Mehrmandanten-Betrieb des Cloud-Overlays darf er nicht versehentlich
mitkommen. Der Router ist bereits über `require_single_token` geschützt und
wird im Overlay bewusst nicht eingehängt — dieselbe Grenze, hinter der schon
`ingest`, `compile` und das Löschen von Rohdaten liegen.

## Inhalt des Archivs

Alles unter `illico-data/` rekursiv, ausgenommen:

- `*.tmp` — halb geschriebene Manifeste aus dem atomaren Schreibmuster
- `.DS_Store`
- `chats/` — nur wenn abgewählt

Im Archiv liegt alles unter einem Wurzelordner `illico-data/`. Dateiname:
`illico-export-YYYYMMDD-HHMM.zip`, mit Uhrzeit, damit zwei Sicherungen am
selben Tag nicht kollidieren.

Ein typisches Archiv enthält damit `raw/`, `wiki/` (samt Sprachvarianten),
`distill/`, `graph/`, `chats/`, `_documents.json`, `_inventory.json`,
`_crawl-status.json` und `_crawl-history.json`.

## Fehler und Grenzen

| Fall | Verhalten |
|---|---|
| Datenverzeichnis fehlt | CLI: Meldung und Exit 1. API: HTTP 404 |
| Zielpfad nicht schreibbar | CLI: Meldung und Exit 1 |
| Zieldatei existiert bereits | CLI: Meldung und Exit 1, kein Überschreiben |
| Temp-Datei nach dem Senden | Wird über einen `BackgroundTask` gelöscht |

**Ein Export während eines laufenden `ingest` oder `compile` ist kein
konsistenter Snapshot.** Dateien können sich währenddessen ändern.

Die Antwort ist ein ZIP-Datenstrom und kann keinen Hinweis im Rumpf tragen.
Läuft beim Aufruf ein Job, setzt die Route deshalb den Header
`X-Illico-Warning` mit Job-Art und -ID; das Web-Interface liest ihn aus und
zeigt ihn neben dem Download an. Der Export wird nicht blockiert — die
Entscheidung gehört dem Nutzer, aber er soll sie bewusst treffen können.

Die CLI läuft in einem eigenen Prozess und kennt die Jobs des Servers nicht.
Dort entfällt der Hinweis; das gehört in die README, damit niemand ihn dort
sucht.

## Bewusst nicht gebaut

Kein Import-Kommando (siehe oben), keine Verschlüsselung, kein
passwortgeschütztes Archiv, keine inkrementellen Sicherungen, kein
Job-Mechanismus, keine Auswahl einzelner Domains. Der Zweck ist der
vollständige Bestand; alles andere wäre eine andere Funktion.

## Tests

**Kern (`write_export`)**

- Alle Dateien landen im Archiv, mit relativer Struktur unter `illico-data/`
- `chats=True` nimmt `chats/` mit, `chats=False` lässt es weg — und lässt den
  Rest unangetastet
- `*.tmp` und `.DS_Store` landen nicht im Archiv
- Fehlendes Datenverzeichnis wirft einen klaren Fehler statt ein leeres Archiv
  zu schreiben
- Ein leeres, aber vorhandenes Datenverzeichnis ergibt ein gültiges leeres
  Archiv

**Route**

- Liefert `application/zip` mit einem Dateinamen im `Content-Disposition`
- Die Temp-Datei ist nach der Antwort verschwunden
- `chats=false` kommt an der Kernfunktion an
- Läuft ein Job, trägt die Antwort `X-Illico-Warning`; läuft keiner, fehlt der
  Header — sonst wäre die Warnung Dauerzustand und würde überlesen

**CLI**

- Schreibt an den mit `-o` angegebenen Pfad
- Ohne `-o` entsteht `illico-export-<zeitstempel>.zip` im aktuellen Verzeichnis
- Eine bestehende Zieldatei bleibt unangetastet, der Aufruf endet mit Exit 1
- `--no-chats` wird durchgereicht
