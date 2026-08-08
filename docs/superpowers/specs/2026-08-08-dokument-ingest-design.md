# Dokument-Ingest über Vision-LLM — Entwurf

**Datum:** 2026-08-08
**Status:** abgenommen, bereit für die Umsetzungsplanung
**Betrifft:** `illico-pub` (Single). Der Cloud-Overlay ist nicht Teil dieser Spec.

## Warum

Illico verarbeitet heute ausschließlich URLs — `ingest` crawlt eine Domain,
`collection` holt eine Bookmark-Liste. Dokumente, die auf der Platte liegen,
haben keinen Weg hinein. Der Bestand, um den es geht, ist **gemischt**: teils
digital erzeugte PDFs mit Textebene, teils Scans.

Alles ab `raw/*.md` ist inhaltsagnostisch. Destillation, Clusterung, Graph und
Chat müssen nicht angefasst werden — ein Dokument-Ingest muss nur Markdown mit
passendem Frontmatter nach `raw/` schreiben.

## Entscheidungen

| Frage | Entscheidung | Begründung |
|---|---|---|
| Dokumenttyp | Gemischt, auch Scans | Vorgabe des Bestands |
| Eingangsweg | CLI zuerst, Web-Upload als spätere Phase | Kleinster Schnitt, kein neuer API- oder UI-Code |
| Herkunft (`domain:`) | Frei wählbares `--label` | Mehrere Bestände bleiben trennbar, `--only-domains` funktioniert unverändert |
| Einheit in `raw/` | Eine PDF-Seite je Datei | Vorhersagbar, per-Seite cachebar |
| Extraktion | Text zuerst, Vision als Rückfall | Digitale Seiten kosten nichts, Scans bekommen volle Auflösung |

### Warum kein PDF-Framework

Docling, Marker und MinerU ziehen alle PyTorch bzw. PaddlePaddle nach. Das
Image hat heute keine ML-Abhängigkeit; mit Docling landet es bei ~9,7 GB, mit
CPU-Wheels und Mühe bei ~2,9 GB. Das beendet „turnkey per `docker compose up`".

PyMuPDF4LLM wäre technisch die beste leichte Option, steht aber unter
**AGPL-3.0** — mit einem kommerziellen Cloud-Overlay heißt das Artifex-Lizenz
kaufen oder offenlegen.

Dazu kommt: Illico schickt jede Rohseite ohnehin durch die Destillation. Was in
`raw/` landet, wird nie wörtlich ausgeliefert, sondern zusammengefasst.
Fidelity-Genauigkeit ist damit deutlich weniger wert als bei klassischem RAG.

## Abhängigkeiten

Zwei neue:

**`pypdfium2`** (Apache-2.0 / BSD-3-Clause) — Bindings an PDFium, Googles
Engine aus Chromium. Selbst ohne jede Laufzeitabhängigkeit, fertige Wheels für
alle Zielplattformen, kein Poppler, kein Ghostscript, kein Torch. Kann beides,
was gebraucht wird: eingebetteten Text extrahieren und Seiten rendern.

**`Pillow`** (HPND, permissiv) — nur als PNG-Encoder. pypdfium2 gibt ein
Bitmap heraus, das sich über `to_pil()` oder `to_numpy()` abholen lässt; für
die base64-Data-URI brauchen wir kodierte PNG-Bytes. Pillow ist der kleinere
der beiden Wege (numpy wäre größer und bräuchte trotzdem noch einen Encoder).
Ein eigener Mini-PNG-Encoder über `zlib` wäre möglich und spart ~3,5 MB, ist
aber selbstgeschriebener Bildcode für wenig Gegenwert — bewusst nicht gemacht.

Verifiziert gegen pypdfium2 **5.12.1**: `PdfDocument(input, password=None)`,
`PdfiumError`, `page.get_textpage()`, `textpage.get_text_bounded()` /
`count_chars()`, `page.render(scale=…)`, `bitmap.to_pil()`.

`illico_llm.py` bleibt **unverändert**. `call_sync` nimmt bereits
`messages: list[dict]` im vollen litellm-Format, also auch multimodale Inhalte.
Der Dokumentpfad ruft es direkt auf und erbt Backoff, Retry und die
529-Behandlung. (`call_llm` in `illico_compile.py:672` ist nur ein
String-Wrapper darüber und wird hier nicht verwendet.)

## CLI

```bash
illico-ingest documents ./ordner --label handbuecher
```

Der Befehl wird in `illico_ingest.py` als weiterer Subcommand deklariert —
analog zu `migrate-lang` — und delegiert sofort an `illico_documents.py`.

| Option | Default | Bedeutung |
|---|---|---|
| `--data` | `./illico-data` | Datenverzeichnis |
| `--label` | *Pflicht* | Wird zur `domain:` und zum Ablagepfad |
| `--model` | `anthropic/claude-sonnet-5` | Modell für die Vision-Extraktion |
| `--jobs` | 4 | Parallele LLM-Aufrufe |
| `--fresh` | aus | Extraktions-Cache ignorieren |
| `--max-pages` | keins | Harte Obergrenze neu extrahierter Seiten **über den ganzen Lauf**, nicht je Dokument |
| `--text-threshold` | 200 | Ab wie vielen Zeichen eine Seite als Textseite gilt |
| `--force-vision` | aus | Weiche abschalten, jede Seite über Vision |

**`--model` erbt bewusst nicht `ILLICO_ANSWER_MODEL`.** Illicos Default ist
Haiku 4.5, und das liegt nicht in der Hochauflösungs-Klasse: Sonnet 5
verarbeitet 2576 px an der langen Kante, Haiku bleibt bei 1568 px. Bei Scans
entscheidet genau das über die Lesbarkeit. Der Präfix `anthropic/` folgt der
Konvention aus `illico_llm.py`.

## Modul und Bausteine

Neues Modul `illico_documents.py`. `illico_ingest.py` hat bereits ~800 Zeilen,
und der Dokumentpfad teilt mit dem Crawler nur die Frontmatter-Konvention und
die Spracherkennung.

| Funktion | Aufgabe |
|---|---|
| `extract_text(page)` | Eingebetteter Text via pdfium-Textpage |
| `is_text_sufficient(text, threshold)` | Die Weiche. Reine Funktion, kein I/O |
| `render_page(page, dpi)` | Seite → PNG-Bytes |
| `page_to_markdown(...)` | Orchestriert die Weiche für **eine** Seite |
| `ingest_documents(paths, ...)` | Treiber: Dateien finden, Cache prüfen, parallelisieren, schreiben |

### Dateiauswahl und Namensbildung

Der Pfad wird **rekursiv** nach `*.pdf` durchsucht (Groß-/Kleinschreibung
egal). Andere Dateien werden übersprungen und am Ende gezählt gemeldet, nicht
still verworfen. Ein einzelner Dateipfad statt eines Verzeichnisses ist
ebenfalls zulässig.

Der Dokument-Slug entsteht aus dem Pfad **relativ zum übergebenen Verzeichnis**,
nicht aus dem bloßen Dateinamen — sonst kollidieren `2024/bericht.pdf` und
`2025/bericht.pdf` zu derselben Datei. Verzeichnistrenner werden Teil des Slugs.
Die Seitennummer wird auf die Breite der höchsten Seitenzahl des Dokuments
nullgepolstert (`--s047` bei 312 Seiten).

### Parallelität — pdfium bleibt einsträngig

PDFium ist nicht ohne Weiteres threadsicher. Textextraktion und Rendering
laufen deshalb **sequenziell im Hauptthread** (beides ist CPU-gebunden und
schnell); nur die Vision-Aufrufe werden über einen `ThreadPoolExecutor` mit
`--jobs` gefächert. Dort liegt ohnehin die gesamte Wartezeit.

### Auflösung

Gerendert wird mit **200 dpi**. Eine A4-Seite ergibt damit 1654 × 2338 px, eine
US-Letter-Seite 1700 × 2200 px — beide unter Sonnet 5s Grenze von 2576 px an
der langen Kante, also ohne Herunterskalieren und mit etwas Luft. PNG statt
JPEG, weil die Token-Kosten an den Abmessungen hängen, nicht an der Dateigröße,
und Kompressionsartefakte auf Text nichts bringen.

`max_tokens` für den Seitenaufruf: 4000, wie beim Artikel-Prompt im Compile.
`call_sync` protokolliert bereits eine Warnung, wenn die Antwort am Limit
abgeschnitten wird.

## Datenfluss

```
PDF ─ pypdfium2 ─┬─ Text vorhanden?  ja  → Markdown = Text        (0 LLM-Aufrufe)
                 └─                  nein → Render 200 dpi → Vision → Markdown
                       ↓
        Frontmatter + Spracherkennung (langdetect, wie im Crawler)
                       ↓
        raw/<label>/<dokument>--s047.md
                       ↓
        _documents.json fortschreiben
                       ↓
        danach unverändert: illico-compile
```

## Frontmatter-Vertrag

```yaml
---
title: "Betriebshandbuch — Seite 47"
source_url: "file://handbuecher/betriebshandbuch.pdf#page=47"
domain: "handbuecher"
crawled: "2026-08-08"
language: "de"
---
```

- `domain` ist das Label. Daran hängen Ablagepfad, `--only-domains` und im
  Cloud-Overlay die Mandantentrennung. `extract_raw_domain`
  (`illico_frontmatter.py:86`) liest es direkt.
- `language` wird gefüllt, damit `--lang` im Compile greift.
- `title` stammt aus den PDF-Metadaten, sonst aus dem Dateinamen.
- `source_url` als `file://…#page=N`. Es wird nur angezeigt und für den
  Domain-Fallback gelesen, nie abgerufen — **bei der Umsetzung verifizieren**,
  dass kein Codepfad `source_url` tatsächlich zu holen versucht.

## Extraktions-Cache

`illico-data/_documents.json`, neben `_crawl-history.json`:

```json
{"handbuecher/betriebshandbuch.pdf": {"hash": "sha256:abc…",
                                      "label": "handbuecher",
                                      "pages_total": 312,
                                      "pages_done": [1, 2, 3]}}
```

**Schlüssel ist `<label>/<Quellpfad relativ zur Wurzel>`, der Hash steht im
Eintrag.** Das Label gehört in den Schlüssel, weil `_documents.json` **eine**
Datei für alle Labels ist: ohne es teilen sich zwei Ingests mit gleicher
relativer Struktur einen Eintrag, und das zweite Dokument bekäme keine
`raw/`-Dateien — derselbe stille Verlust wie beim Hash-Schlüssel, nur eine
Ebene höher. Aus demselben Grund löscht `--fresh` nur die Einträge des
laufenden Labels, nicht das ganze Manifest.

**Bekannte Grenze:** dasselbe Label für zwei verschiedene Quellverzeichnisse
mit gleicher interner Struktur kollidiert weiterhin — dann stimmen auch die
Slugs überein und die Dokumente überschreiben sich in `raw/`. Das Label ist der
Namensraum; zwei Bestände gehören unter zwei Labels. Der Hash-Vergleich macht
daraus immerhin Neuextraktion statt stillem Überspringen.
Der Hash ist der Änderungsdetektor: stimmt er, wird das Dokument übersprungen;
weicht er ab, werden alle seine Seiten neu geholt.

Erkannt wird also weiterhin an den **PDF-Bytes**, nicht am erzeugten Markdown —
das ist der Kern und bleibt unberührt: ein Vision-LLM produziert bei jedem Lauf
leicht anderes Markdown, und `content_hash` (`illico_distill.py:22`) hasht
Frontmatter plus Rumpf. Ohne diesen Cache würde jeder erneute Lauf alle
Destillate invalidieren. Mit ihm wird `raw/` pro Dokumentversion genau einmal
geschrieben, und die Kette dahinter merkt nichts.

**Warum nicht der Hash als Schlüssel** (so stand es im ersten Entwurf): die
Ausgabedateinamen leiten sich aus dem *Pfad* ab. Lägen zwei byte-gleiche PDFs
an zwei Pfaden, teilten sie sich einen Manifest-Eintrag — und das zweite bekäme
überhaupt keine `raw/`-Dateien, weil der Eintrag des ersten es als erledigt
ausweist. Das Dokument verschwände still aus dem Wiki. Ein Dokument ist durch
seinen Pfad identifiziert, nicht durch seinen Inhalt; der Inhalt sagt nur, ob
es sich geändert hat.

**Nicht gelöst:** wird ein PDF umbenannt, entsteht ein neuer Eintrag und die
Seiten werden neu extrahiert; die Dateien unter dem alten Slug bleiben als
Waisen liegen. Das gilt für jede Schlüsselwahl gleichermaßen und ist eine
eigene Runde wert, wenn es auftritt.

`pages_done` ist der zweite Zweck: bricht Seite 150 von 312 ab, holt der
nächste Lauf nur die fehlenden nach — dieselbe Fehlertoleranz, die `distill_all`
schon hat.

**Bewusst nicht gebaut:** kein Per-Seite-Store über Dokumentversionen hinweg.
Ändert sich ein PDF, werden seine Seiten neu extrahiert. Seitenzahlen können
beim Reflow verrutschen, und geänderte PDFs sind der seltene Fall.

## Die Weiche und ihre Grenze

Eine Seite gilt als Textseite, wenn der extrahierte Text mindestens
`--text-threshold` Zeichen hat (Default 200).

**Bekannte Fehlfunktion:** eine Seite mit Kopfzeile und einer großen
gescannten Abbildung überschreitet die Schwelle und verliert die Abbildung.
Zwei Ventile: `--text-threshold` ist verstellbar, `--force-vision` schaltet die
Weiche für einen bekannt gescannten Bestand ganz ab.

Der Lauf meldet am Ende, wie viele Seiten welchen Weg genommen haben. Stille
Quoten wären hier genau falsch — die Vision-Zahl ist zugleich die Kostenzeile.

## Fehlerbehandlung

Dieselbe Haltung wie im Crawler und in `distill_all`: ein kaputtes Element darf
den Lauf nicht töten.

| Fall | Verhalten |
|---|---|
| Datei defekt, kein PDF, nicht lesbar | Dokument überspringen, zählen, weiter |
| Verschlüsselt ohne Passwort | Überspringen, klar benennen (kein `--password` in v1) |
| Render einer Seite schlägt fehl | Seite als fehlgeschlagen zählen, weiter |
| LLM-Fehler auf einer Seite | Seite fehlgeschlagen, weiter — nächster Lauf holt sie über `pages_done` nach |
| Leere oder unbrauchbare Antwort | Wie LLM-Fehler, damit sie nicht als Erfolg im Cache landet |
| `LLMAuthError` | Sofort abbrechen, Exit 1 — wie `compile` |
| Keine Dateien gefunden | Exit 1 mit Hinweis auf den Pfad |
| Rate Limit, 529, Timeout | Kein eigener Code — `call_sync` erledigt Backoff und Retry |

Abschlussbilanz: *n* Dokumente, *m* Seiten, davon *x* aus Text, *y* über Vision,
*z* fehlgeschlagen.

## Tests

**Reine Funktionen:** `is_text_sufficient` an der Schwelle (leer, nur
Whitespace, 199, 200, darüber); Dateinamen stabil und nullgepolstert;
Frontmatter korrekt (Label → `domain`, Seitenzahl in Titel und `source_url`).

**Weichenlogik**, mit einem gefälschten LLM nach dem Muster von `ScriptedLLM`
aus `tests/test_compile_incremental_e2e.py`:

- Seite mit Textebene → **0 LLM-Aufrufe**, Markdown ist der extrahierte Text
- Seite ohne Textebene → **genau 1 Aufruf**
- `--force-vision` → auch die Textseite geht über Vision
- `--text-threshold` verschiebt die Grenze nachweislich

**Cache:**

- Zweiter Lauf über unverändertes PDF → 0 Aufrufe, keine Datei in `raw/` neu geschrieben
- `--fresh` umgeht den Cache
- Seite 2 von 3 scheitert → Lauf endet mit 0 und meldet es; zweiter Lauf macht **genau einen** Aufruf

**Integration:** die erzeugten `raw/`-Dateien durch die bestehende
Compile-E2E-Harness schicken und prüfen, dass ein Wiki herauskommt — inklusive
`--only-domains handbuecher` und `--lang`.

**Testentscheidung:** Es wird nicht geprüft, ob pdfium bei einem Scan wirklich
leeren Text liefert — das ist pdfiums Sache. Die Weichentests hängen sich an
die Naht `extract_text` und stubben sie. Dazu **ein** Smoke-Test mit einem
echten Mini-PDF, der bestätigt, dass pypdfium2 richtig verdrahtet ist. Das
erspart binäre Fixtures für jeden Fall und testet trotzdem, was uns gehört.

Kein Test geht ins Netz — das LLM ist immer gefälscht, wie überall in der Suite.

**Bestehende Wächter, die ohne Änderung greifen:**
`test_packaging_completeness.py` schlägt an, wenn `illico_documents.py` in
`pyproject.toml` unter `only-include` fehlt; `test_import_boundary.py` wacht
darüber, dass das neue Modul keine Cloud-Module zieht.

## Kosten

Pro Seite, bei ~1000 Tokens Markdown Ausgabe:

| Modell | Preis | pro Seite | pro 1000 Seiten |
|---|---|---|---|
| Sonnet 5 | $3 / $15 pro MTok | ~$0,030 | ~$30 |
| Sonnet 5 (Intro bis 31.08.2026) | $2 / $10 | ~$0,020 | ~$20 |
| Haiku 4.5 | $1 / $5 | ~$0,007 | ~$7 |

Das gilt nur für Seiten, die über Vision laufen. Bei einem Bestand, der zu zwei
Dritteln digital erzeugt ist, drittelt die Weiche diese Rechnung.

## Nicht in dieser Phase

- **Web-Upload.** Eigene Phase. Die CLI ist dann die Maschine, die der
  Endpunkt nur noch anstößt.
- **Batch-API.** Gibt 50 % Rabatt, und Dokument-Ingest ist nicht latenzkritisch
  — aber Polling über bis zu 24 Stunden passt schlecht in einen CLI-Aufruf.
  Lohnt eine eigene Betrachtung, wenn die Mengen groß werden.
- **Prompt-Caching.** Bringt hier nichts, weil jedes Seitenbild neu ist.
- **Abschnittsweise Bündelung.** Zusammengehörende Seiten anhand der
  Überschriften zu Abschnitten zusammenzufassen wäre näher an einer Webseite,
  wurde aber zugunsten der einfacheren Seiteneinheit zurückgestellt.
- **Office-Formate.** DOCX, PPTX, XLSX sind nicht Teil dieser Phase. Der
  Extraktor ist so geschnitten, dass ein zweiter Reader daneben passt.
