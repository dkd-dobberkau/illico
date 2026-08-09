# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in dieser Datei dokumentiert.

## Unreleased — Bestands-Export, und was der erste echte Lauf zutage förderte

**Neu: `illico-export`.** Das Datenverzeichnis lässt sich als ZIP sichern —
über die Kommandozeile, über `GET /api/export` oder über den Knopf „Bestand
exportieren" im Web-Interface. Das Archiv enthält den kompletten Bestand
einschließlich der Destillate und Manifeste, denn jedes Destillat ist ein
bezahlter Modellaufruf: ein Archiv aus nur `raw/` und `wiki/` sähe vollständig
aus und zwänge die Zielmaschine trotzdem, alles neu zu destillieren.
Zurückspielen braucht keinen eigenen Befehl, das Archiv entpackt sich nach
`illico-data/`.

**Achtung beim Aktualisieren:** Die Exportdatei bekommt die Rechte `0600`
statt der bisher üblichen `0644`. Das ist die Folge des Schutzes gegen zwei
gleichzeitige Exporte auf dieselbe Zieldatei und für ein Archiv mit
Chatverläufen die sicherere Voreinstellung — ein Cron-Job, der die Sicherung
anschließend als anderer Nutzer weiterreicht, muss das berücksichtigen.

**`--max-pages` deckelt jetzt Kosten statt Seiten.** Beim Dokument-Ingest
rechnete die Obergrenze gegen alle offenen Seiten statt gegen die
kostenpflichtigen; `--max-pages 5` verarbeitete fünf Seiten und verbrauchte
dabei trotzdem zwei Vision-Aufrufe. Als Schutz vor einem Vision-Sturm war der
Deckel damit wirkungslos. Seiten mit Textebene laufen jetzt immer durch, weil
sie nichts kosten, und die neue Bilanzzeile `Vision-Aufrufe` weist die
tatsächlich bezahlte Menge aus — sie liegt über `Ueber Vision`, weil auch
Seiten bezahlt sind, die das Modell als leer zurückmeldet.

**Der Compile verliert keine Seiten mehr und sagt, wenn doch.** Ein
gescheiterter Destillations-Batch nahm bisher fünfzehn Seiten mit und
hinterließ keine Spur — über drei Läufe blieben dieselben Seiten liegen, ohne
dass erkennbar war, warum. Fehler nennen jetzt ihre Ursache, und ein an der
Token-Grenze abgeschnittener Batch reicht die fehlenden Seiten in kleineren
Häppchen nach. Auf einem Testbestand stieg die Abdeckung damit von 81 auf 100
Prozent.

**Artikel tragen wieder brauchbare Metadaten.** Das `compiled:`-Datum kam vom
Modell und war frei erfunden; der Kopf wurde außerdem mal als Frontmatter, mal
als ```yaml-Codeblock geliefert, was weder gültiges Frontmatter noch einen
geschlossenen Codeblock ergab. Beides setzt der Compile jetzt selbst.

## v0.3.4 — Sprachläufe teilen sich kein Inventar mehr

`--lang de` und `--lang en` legen Wiki, Graph und Destillat-Store getrennt ab —
das Inventar aber nicht. Es hieß in beiden Fällen `_inventory.json`, weil der
Sprachsuffix an drei Stellen unabhängig voneinander berechnet wurde und
ausgerechnet an dieser einen durchfiel. Der zweite Sprachlauf lud damit die
Cluster des ersten, fand deren Destillate nicht wieder und räumte sie weg. Der
nächste Lauf der ersten Sprache schnitt seine Themen daraufhin komplett neu:
statt null LLM-Aufrufen kostete er Zuordnung, sämtliche Artikel, Index und Lint.
Genau die Ersparnis, für die v0.3.0 gebaut wurde, war bei mehrsprachigem Betrieb
also nie wirksam.

Graph, Inventar und Destillat-Store leiten ihren Namen jetzt über eine einzige
Funktion (`_wiki_suffix`) aus dem Wiki-Verzeichnis ab. Damit entfällt auch die
Fallunterscheidung zwischen `--lang` und `--wiki-dir` — beide Wege laufen durch
dieselbe Ableitung, und ein künftiger Pfad kann nicht mehr einzeln ausscheren.

**Migration:** Wer bisher mit `--lang` gearbeitet hat, besitzt ein
`_inventory.json`, das nun nicht mehr gefunden wird. Der erste Lauf nach dem
Update schneidet die Themen deshalb einmalig neu — die Artikel unter den alten
Slugs werden dabei als Waisen entfernt und neu geschrieben, `[[Links]]` und
Bookmarks in diesem Wiki ändern sich also ein Mal. Wikis ohne `--lang` sind
nicht betroffen. Die alte `_inventory.json` bleibt liegen und kann danach
gelöscht werden; Illico benennt sie bewusst nicht automatisch um, weil bei einem
bereits vermischten Bestand nicht entscheidbar ist, welcher Sprache sie gehört.

Außerdem: beide READMEs auf den Stand von 0.3.x gebracht — Destillat-Schicht und
inkrementeller Compile, die Optionen `--jobs`, `--graph-only`,
`--canonicalize-only` und `--only-domains`, ein Abschnitt zur Mehrsprachigkeit,
der vollständige Datenverzeichnis-Baum und die Prompt-Sprachen. Die englische
Fassung war noch auf 0.2-Stand und ist jetzt inhaltlich gleichgezogen.

## v0.3.3 — Erstaufbau clustert wieder thematisch

Der Zuordnungs-Prompt war für den **inkrementellen** Fall geschrieben:
„Bevorzuge bestehende Cluster, lege nur einen neuen an, wenn wirklich keiner
passt." Beim Erstaufbau ist das genau die falsche Anweisung — es gibt keine
bestehenden Cluster, also legte das LLM einen Sammel-Cluster für alles an. Ein
Tenant mit 5 Seiten bekam so einen einzigen Artikel statt drei; je kleiner die
Site, desto grober das Ergebnis.

`_build_assign_prompt` schreibt jetzt einen sprachneutralen Zustandsmarker
(`INVENTORY-STATE: EMPTY` bzw. `INVENTORY-STATE: <Zahl>`) in den Prompt, und
beide Sprachvarianten unterscheiden daran zwei Regeln: beim Erstaufbau
thematisch aufteilen (Richtwert ein Cluster je 3–10 Dokumente, höchstens 15 pro
Antwort, ein Sammel-Cluster ist explizit falsch), danach bestehende Cluster
bevorzugen. Der Marker ist bewusst maschinell, damit der Prompt-Bau die
Prompt-Sprache nicht kennen muss.

An einer 5-Seiten-Site gemessen: vorher 1 Cluster, nachher 3.

## v0.3.2 — Migrations-Fix: verwaiste Artikel werden entfernt

Beim ersten Lauf nach der Umstellung wird das Inventar neu geschnitten und
vergibt neue Slugs. Die Artikel unter den **alten** Slugs gehörten danach zu
keinem Cluster mehr — und weil v0.3.0 das Pauschal-Löschen bewusst abgeschafft
hat, blieben sie liegen. Ein bestehendes Wiki hätte nach dem ersten Lauf alte
und neue Artikel nebeneinander enthalten.

`phase_articles` entfernt jetzt Artikeldateien, zu denen es keinen Cluster im
Inventar gibt. Zwei Sicherungen dagegen, dass daraus ein Kahlschlag wird:

- Ein **leeres** Inventar löst gar nichts aus — das heißt „etwas ist
  schiefgegangen", nicht „lösch alles". Sonst räumte ein gescheiterter
  Zuordnungsschritt das ganze Wiki leer.
- Underscore-Dateien (`_index.md`, `_lint-report.md`) sind ausgenommen.

Entfernte Artikel zählen als Änderung und lösen Index und Lint aus — sonst
zeigte der Index weiter auf Dateien, die es nicht mehr gibt. `phase_articles`
liefert sie deshalb im zweiten Rückgabewert mit.

Die separate Behandlung leer gewordener Cluster im Orchestrator entfällt: `prune`
entfernt sie vor der Artikel-Phase aus dem Inventar, womit ihre Artikel ohnehin
Waisen sind.

## v0.3.1 — Packaging-Fix (v0.3.0 nicht verwenden)

**v0.3.0 ist unbrauchbar und wurde zurückgezogen.** Die Wheel-Whitelist in
`pyproject.toml` (`only-include`) ist explizit, und die beiden neuen Module
`illico_distill.py` und `illico_inventory.py` fehlten darin — sie wurden nicht
ausgeliefert. `import illico_compile` funktionierte weiterhin (die Module werden
lazy importiert), ein echter Compile brach dann aber mit `ModuleNotFoundError`
ab. Wer v0.3.0 installiert hat, hebt bitte auf v0.3.1.

Die CI hatte keine Chance, das zu sehen: sie führt pytest gegen den Quellbaum
aus, nie gegen das gebaute Paket. Neu ist deshalb
`tests/test_packaging_completeness.py` — es vergleicht die Module im
Repo-Wurzelverzeichnis mit der Whitelist und schlägt in beide Richtungen an
(vergessenes Modul, verwaister Eintrag).

## v0.3.0 — Inkrementeller Compile über eine Destillat-Schicht

Der Compile verarbeitete bisher bei jedem Lauf die gesamte Site neu. Bei ~16.000
gecrawlten Seiten waren das rund 1.000 Graph-Extraktions-Calls plus ein
Artikel-Call je Cluster — strikt sequenziell, ohne Wiederaufsetzen, und ein
Nachcrawl mit 50 neuen Seiten kostete denselben vollen Preis wie der Erstlauf.

- **Destillat-Schicht (neu: `illico_distill.py`):** Jede Rohseite wird einmal zu
  einem Destillat verdichtet (Kurzfassung, Kernaussagen, Entitäten, Beziehungen)
  und unter `distill*/v1/<hash>.json` gecacht. Der Hash deckt Rumpf und
  Frontmatter ab, **ignoriert aber `crawled:`** — sonst würde jeder Nachcrawl den
  gesamten Cache invalidieren. Batches à 15 Seiten, parallelisiert über `--jobs`.
- **Persistiertes Inventar (neu: `illico_inventory.py`):** `_inventory*.json` ist
  jetzt Zustand statt Debug-Artefakt. Slugs sind **unveränderlich**, damit
  `[[links]]` und Bookmarks Compiles überleben; ein Fingerprint über die
  Mitglieder entscheidet deterministisch, welche Artikel neu geschrieben werden.
- **Kein Pauschal-Löschen mehr:** Bisher entfernte die Artikel-Phase zuerst alle
  Artikel. Das Wiki war dadurch während jedes Laufs unvollständig und nach einem
  Abbruch ein Torso. Artikel verschwinden jetzt nur, wenn ihr Cluster leer wird.
- **Graph aus den Destillaten:** `phase_graph` merged die Entitäten der
  Destillate und kanonisiert sie; der eigene Vollscan über den Rohtext entfällt
  ersatzlos (`EXTRACT_PROMPT`, `MERGE_GRAPH_PROMPT`, `_extract_graph_batch`).
  Bei unveränderten Destillaten wird die Phase komplett übersprungen.
- **Fehlertoleranz:** Ein Batch, der auch nach Retries scheitert, kostet nur
  seine Seiten statt den ganzen Lauf. Sie werden gezählt, berichtet und beim
  nächsten Lauf erneut versucht — der Cache heilt sich selbst.
- **`--jobs N`** (Default 4) parallelisiert Destillation und Artikel-Erzeugung.
  Die Cluster-Zuordnung bleibt bewusst seriell, sonst erfinden zwei Batches
  unabhängig denselben Cluster.
- **Sichere Slugs:** Cluster-Slugs und Titel stammen aus LLM-Output und landen
  als Dateiname. `slugify()` transliteriert Umlaute und entfernt alles, was ein
  Pfad sein könnte.

Gemessen an einer realen Site mit 14 Seiten: Erstlauf 1:53, zweiter Lauf ohne
Änderungen **0,94 s ohne einen einzigen LLM-Aufruf**, Nachcrawl ohne inhaltliche
Änderung 1,3 s, eine geänderte Seite berührt genau einen von fünf Artikeln.

**Der Erstlauf wird dadurch nicht billiger, eher etwas teurer** — ein Destillat
produziert mehr Output als die bisherige Graph-Extraktion allein. Der Gewinn
liegt in den Folgeläufen und darin, dass Artikel nicht mehr aus maximal fünf auf
2.000 Zeichen gekürzten Quelldateien entstehen, sondern aus allen Destillaten
ihres Clusters.

### Breaking Changes

- `phase_graph(distillates, …)` statt `phase_graph(raw_files, …)`
- `phase_articles(distillates, inventory, previous, …) -> (created, written)`
- `phase_index(…, lang="")` nimmt die Quellsprache entgegen
- Die `Prompts`-Felder `extract`/`merge_graph` weichen `distill`/`assign`
- `_inventory*.json` hat ein neues Format. Alt-Inventare ohne `schema` werden
  erkannt und einmalig neu aufgebaut; bestehende Wikis bleiben unangetastet, bis
  sie bewusst neu kompiliert werden.

### Vollneubau erzwingen

`distill*/` löschen re-destilliert alles (etwa nach einem Modellwechsel — der
invalidiert den Cache bewusst nicht). `_inventory*.json` löschen schneidet die
Themencluster neu, wobei die alten Slugs verloren gehen.

## v0.2.3 — Collection-/Bookmark-Modus, Docker-Compose, englische README

- **Neuer Ingest-Modus `collection`:** Statt eine Domain zu crawlen, verarbeitet
  `illico-ingest collection <bookmarks.html>` eine kuratierte URL-Liste aus einem
  Browser-Bookmarks-Export (Netscape-HTML). Jede URL wird genau einmal geholt (kein
  BFS), domain-präfixiert unter `raw/<domain>/…` abgelegt. Optionen analog zu
  `ingest` (`--data`, `--delay`, `--fresh`, `--lang`, `--max-pages`). Der bestehende
  Domain-Crawl bleibt unverändert.
- **Docker-Compose:** `docker-compose.yml` + `.dockerignore` für turnkey Self-Hosting
  — die ganze Pipeline (Crawl/Collection → Compile → Web-UI) im selben Image gegen
  ein persistentes `./illico-data`, Key über `.env`.
- **Englische README** (`README.en.md`, WIP) mit Hinweis, dass die deutsche README
  die maßgebliche Fassung ist. Kleiner Bugfix im deutschen Usage-Beispiel
  (`illico-ingest` braucht den `ingest`-Subcommand).

## v0.2.2 — Fix: Compile überlebt Anthropic-Überlast (HTTP 529)

- **Fix:** Große Compiles brachen bei transienter Provider-Überlast hart ab, statt
  zu retryen. Anthropics `529 overloaded_error` kommt bei litellm als
  `InternalServerError` an — der fehlte in der Retryable-Liste (`illico_llm._RETRYABLE`),
  sodass der Fehler bis nach oben durchschlug und den Compile mittendrin killte.
  Jetzt wird `InternalServerError` mit Exponential-Backoff wiederholt (transient/
  retrybar laut Anthropic-Doku). 2 neue Tests decken Membership und Retry-Verhalten ab.

## v0.2.1 — Fix: Web-Verwaltung bei pip-Installation

- **Fix:** Die Web-Verwaltung (Crawlen/Kompilieren/Graph über die Oberfläche)
  startete die Kern-CLIs per Dateiname (`illico_ingest.py`), was nur aus einem
  Quell-Checkout funktionierte — bei `pip install illico` lagen die Module in
  site-packages und die Aufrufe brachen mit „can't open file". Jetzt paket-sicher
  über `python -m illico_ingest` / `python -m illico_compile` (CWD-unabhängig).
- Kleinere Korrektur eines veralteten Hinweis-Textes im Compiler.

## v0.2.0 — eigenes Single-Frontend + Web-Verwaltung

- **Eigenes, schlankes Single-Frontend** (`illico_index.html`): Chat, Wiki-Browsen,
  Quellen und Lint-Hinweise — ohne Login, ohne Mandanten-/Admin-Ballast.
- **Web-Verwaltung im Browser** (`illico_single`): Crawlen, Kompilieren, Graph neu
  bauen und Domains entfernen direkt aus der Oberfläche, mit Live-Job-Log.
- **Optionaler Zugangs-Token** `ILLICO_SINGLE_TOKEN`: leer = offen (localhost),
  gesetzt = `Authorization: Bearer <token>` für die Verwaltungs-Endpoints
  (konstantzeitiger Vergleich).
- **App-Factory** `create_app(frontend_path=…, management_router=…)` — Frontend und
  Verwaltungs-Router überschreibbar (Naht für private Overlays).
- **Vollständig offline**: d3 ist lokal eingebettet, keine externen CDN-Abhängigkeiten.

## v0.1.0 — erstes öffentliches Release (Illico Single)

- Erste öffentliche Version von Illico als installierbares Python-Paket.
- Pipeline: `illico-ingest` (Crawl) → `illico-compile` (LLM-Wiki-Compiler) →
  `illico-chat` (CLI-Chat) → `illico-serve` (Web-Oberfläche mit FastAPI-Backend).
- Kein RAG, keine Vektor-Datenbank — die Wissensbasis ist ein lesbares,
  Git-versionierbares Markdown-Wiki mit Obsidian-Style `[[Links]]`.
