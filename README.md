# MultipleChoiceTest — Quiz zur Prüfungsvorbereitung

Eine kleine, lokal laufende Web-App zum Üben von Multiple-Choice-Prüfungsfragen.
Sie mischt die Antwortmöglichkeiten, führt eine Statistik und streut falsch
beantwortete Fragen wieder ein, bis sie sitzen. Fragenkataloge sind austauschbar:
mitgeliefert ist ein Katalog zur TISP-Prüfung, weitere lassen sich als JSON-Datei
hochladen.

Server und App brauchen nur Python und einen Browser — keine Pakete, kein Build,
keine Datenbank.

## Schnellstart

```bash
python3 server.py
```

Der Browser öffnet sich mit <http://localhost:8000/quiz.html>. Beenden mit `Strg+C`.

```bash
python3 server.py --port 9000     # anderen Port verwenden
python3 server.py --no-browser    # Browser nicht automatisch öffnen
```

Voraussetzung ist Python 3.7 oder neuer; es wird ausschließlich die
Standardbibliothek verwendet.

> Die HTML-Dateien lassen sich **nicht** per Doppelklick öffnen. Die App lädt ihre
> Fragen über den Server; direkt aus dem Dateisystem geöffnet zeigt sie einen
> entsprechenden Hinweis.

## Mitgelieferter Fragenkatalog

| Katalog | Fragen | Module | Datei |
|---|---:|---:|---|
| TISP | 456 | 24 | `kataloge/tisp.json` |

## Üben

- Zufällige Frage aus dem gewählten Katalog, Mehrfachauswahl möglich.
- Die Antwortmöglichkeiten werden bei jedem Aufruf neu gemischt. Gemischt wird nur
  das angezeigte Label — welche Antwort richtig ist, bleibt an ihrem Text.
- Statistik über Beantwortet, Richtig, Falsch und Quote.
- **Wiederholungs-Pool:** Falsch beantwortete Fragen wandern in einen Pool und
  müssen zweimal hintereinander richtig beantwortet werden, um ihn zu verlassen.
  Mit aktivem Haken „Falsche bevorzugt wiederholen" werden sie häufiger
  eingestreut; die letzten fünf Fragen werden nicht sofort wiederholt.
- Filter auf ein einzelnes Modul.
- Nach dem Prüfen: Auflösung mit allen richtigen Antworten, ein Link zu Claude für
  eine Erklärung des Konzepts und, falls der Katalog es mitbringt, ein Link auf die
  Originalquelle.
- Bei manchen Fragen ist **keine** Antwort richtig. Dann ist es die richtige
  Lösung, nichts auszuwählen und direkt auf „Antwort prüfen" zu drücken.
- Tastatur: `A`, `B`, `C`, … wählen eine Antwort aus, `Enter` prüft und springt
  danach zur nächsten Frage.

Statistik und Wiederholungs-Pool liegen pro Katalog im `localStorage` des Browsers,
nicht auf dem Server.

## Einstellungen

Erreichbar über „⚙ Einstellungen" in der Seitenleiste (<http://localhost:8000/settings.html>):

- **Hinweis zur Anzahl richtiger Antworten** — entweder „2 Antworten sind richtig."
  über der Frage, oder im Prüfungsmodus gar kein Hinweis.
- **Aktiver Fragenkatalog** — Auswahl unter allen vorhandenen Katalogen.
- **Katalog hochladen** — eigene JSON-Datei einlesen; hochgeladene Kataloge lassen
  sich wieder löschen, die mitgelieferten nicht.

Änderungen werden sofort in `settings.json` gespeichert.

## Eigene Fragenkataloge

Ein Objekt mit `name` und `fragen` (so liegen auch die mitgelieferten Kataloge vor)
oder direkt eine Liste von Fragen:

```json
[
  {
    "frage": "Welche Protokolle arbeiten auf Schicht 4 des OSI-Modells?",
    "optionen": {
      "A": "TCP",
      "B": "IP",
      "C": "UDP",
      "D": "HTTP"
    },
    "richtig": ["A", "C"],
    "modul": "Netzwerke",
    "level": 2,
    "lerninhalt": "Transportschicht",
    "quelle": "https://example.org/osi"
  }
]
```

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `frage` | ja | Fragetext |
| `optionen` | ja | 2 bis 26 Antwortmöglichkeiten als Objekt; die Schlüssel sind frei wählbar |
| `richtig` | ja | Liste der richtigen Schlüssel. `[]` bedeutet: keine Antwort ist richtig |
| `modul` | nein | Gruppierung für den Filter; fehlt sie, wird der Katalogname verwendet |
| `nummer` | nein | Kennung der Frage, hilfreich zum Abgleich mit der Quelle |
| `level` | nein | `1`, `2` oder `3` — wird als Badge angezeigt (Basis, Experten, Verständnis) |
| `lerninhalt` | nein | Thema der Frage |
| `quelle` | nein | `http(s)`-Link, wird im Feedback als Quellenlink angeboten |

Der Server prüft die Datei beim Hochladen und meldet Fehler mit Fundstelle, etwa
„Frage 12: 'richtig' enthält unbekannte Option(en): E". Angenommene Kataloge landen
als `kataloge/<name>.json`. Maximale Dateigröße: 20 MB.

Ein Modulname wird in der Anzeige mit Leerzeichen statt Unterstrichen dargestellt.
Eine führende Nummer (`01_…`) sorgt dafür, dass die Module im Filter in der
richtigen Reihenfolge stehen.

## Schnittstelle

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/api/settings` | aktuelle Einstellungen |
| `PUT` | `/api/settings` | Einstellungen speichern |
| `GET` | `/api/catalogs` | alle Kataloge mit Anzahl und Modulen |
| `GET` | `/api/catalogs/<id>` | Fragen eines Katalogs |
| `POST` | `/api/catalogs` | Katalog hochladen (`{"name": …, "fragen": […]}`) |
| `DELETE` | `/api/catalogs/<id>` | hochgeladenen Katalog löschen |

## Projektstruktur

```
server.py        Webserver und JSON-Schnittstelle (nur Standardbibliothek)
quiz.html        die Quiz-Oberfläche
settings.html    Einstellungen und Katalog-Upload
style.css        gemeinsame Gestaltung beider Seiten
kataloge/        die Fragenkataloge (mitgeliefert und hochgeladen)
settings.json    gespeicherte Einstellungen
```

## Hinweise

- Der Server lauscht nur auf `127.0.0.1` und kennt keine Anmeldung. Er ist für den
  lokalen Gebrauch gedacht, nicht für den Betrieb im Netz.
- Der Lernfortschritt hängt an Browser und Adresse. Wer den Port wechselt, beginnt
  mit einer neuen Statistik.

## Herkunft der Fragen

Prüfungsfragen stammen aus Erfahrungsberichten erfolgreicher Absolventen der
jeweiligen Prüfungen.

## Lizenz

[MIT](LICENSE) für den Code dieses Projekts.
