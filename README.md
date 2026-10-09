# Stromvergleich

Der Nutzer gibt seine **Postleitzahl** und seinen **jährlichen Stromverbrauch** ein.
Die App geht alle Anbieter aus der StromAuskunft-Liste (1.387 Einträge) durch und zeigt live:

- ob Informationen zu dem Anbieter gefunden wurden,
- ob die PLZ beliefert werden kann,
- Grundgebühr (€/Monat) und Arbeitspreis (ct/kWh), **ohne Boni**,
- Jahreskosten = Verbrauch × Arbeitspreis + 12 × Grundgebühr.

Am Ende stehen die **Top 5** nach Jahreskosten und die Anzahl der Anbieter, zu denen Informationen gefunden wurden.

## Schnellstart

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Dann im Browser `http://localhost:8000` öffnen.

Tests:

```bash
pytest -q tests/test_parser.py          # Textauswertung, offline
python tests/smoke_check.py 50667 1200  # echte Websites, braucht Internet
```

## Deployment auf Render.com

**Problem:** `pip install playwright` installiert nur das Python-Paket, nicht den
Browser. Und selbst ein `playwright install` im Build-Schritt reicht bei Render
nicht: Build und Runtime teilen sich nur das Projektverzeichnis inkl. `.venv`.
Der übliche Ablageort `~/.cache/ms-playwright` steht zur Laufzeit nicht mehr zur
Verfügung → `BrowserType.launch: Executable doesn't exist ...` und
„Application startup failed“.

**Lösung:** `PLAYWRIGHT_BROWSERS_PATH=0` – damit packt `playwright install` den
Browser direkt ins installierte `playwright`-Paket im `.venv`, und das wird vom
Build mit in die Runtime genommen. Der Wert muss beim Build **und** zur Laufzeit
gesetzt sein.

Im Render-Dashboard (Settings des Web-Service):

| Feld | Wert |
|---|---|
| Build Command | `pip install -r requirements.txt && PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Environment Variable `PLAYWRIGHT_BROWSERS_PATH` | `0` |

Alternativ dient die `render.yaml` im Repo als Blueprint (Dashboard →
„New Blueprint“ bzw. „Save as Blueprint“), dann sind die Werte schon gesetzt.

Hinweise:

- **Ein Worker:** Der Start-Befehl absichtlich ohne gunicorn-Multi-Worker – die
  App hält Browser, Cache und Semaphor im Speicher.
- **RAM:** Chromium ist speicherhungrig. Bei OOM-Abstürzen `MAX_PARALLEL` in
  `app/main.py` (3) auf 2 oder 1 senken; für den Free-Plan ist das ratsam.
- **Fehlende Systembibliotheken:** Falls der Start mit
  `error while loading shared libraries ...` scheitert, im Build Command
  `python -m playwright install chromium` durch
  `python -m playwright install --with-deps chromium` ersetzen (benötigt apt im
  Build) oder die App als Docker-Service deployen.
- **Cache:** `data/cache.json` lebt im Projektverzeichnis und ist auf Render
  pro Deploy nur ephemer – nach jedem Deploy beginnt der Cache leer.

## Aufbau

```
app/
  main.py        FastAPI: /  (Oberfläche), /api/providers, /api/compare (Server-Sent Events)
  providers.py   lädt Anbieterliste (CSV) und ordnet Websites zu
  checker.py     Browser-Abfrage pro Anbieter (generisch + Adapter)
  parser.py      Textauswertung: Arbeitspreis/Grundgebühr finden und paaren (ohne Browser testbar)
  static/        Oberfläche (eine HTML-Datei, ohne Build-Schritt)
data/
  anbieter_details.csv   1.387 Anbieter: Name, Sitz (PLZ/Ort) aus StromAuskunft
  domain_guess.json      Websites, die über Domain-Varianten des Namens gefunden wurden (248 NRW-Anbieter geprüft, 104 Treffer; zusammen mit overrides.json 115 Anbieter mit Website)
  overrides.json         manuell gepflegte Websites (Slug -> URL), haben Vorrang
  cache.json             (wird zur Laufzeit angelegt) Tarifdaten, 12 h gültig
tests/
  test_parser.py         Unit-Tests der Textauswertung
  smoke_check.py         Test gegen echte Anbieterseiten
```

### Ablauf einer Anfrage

1. Der Browser öffnet `/api/compare?plz=…&kwh=…`. Der Server antwortet als Stream.
2. Für jeden der 1.387 Anbieter wird eine Zeile gesendet, sobald sie fertig ist.
3. Ohne bekannte Website: Zeile „Keine Website bekannt“, keine Abfrage.
4. Mit Website: Seite im Headless-Browser laden, Cookies akzeptieren, PLZ und Verbrauch eingeben, Ergebnis lesen, Text auswerten.
5. Bis zu 3 Abfragen laufen parallel (`MAX_PARALLEL` in `main.py`).
6. Am Ende: Top 5 = günstigste Jahreskosten unter den Anbietern, die die PLZ beliefern (oder nicht ausgeschlossen sind), plus Statistik.

## Wie die Daten ermittelt werden

- **Anbieterliste:** StromAuskunft, Detailseiten der 1.387 Anbieter (Name, Sitz).
- **Websites:** aus dem Anbieternamen abgeleitete Domains (`name.de`, `www.name.de`), per HTTP geprüft. Nur 104 der 248 NRW-Anbieter haben eine Treffer-Domain. Fehlende Websites lassen sich in `data/overrides.json` nachtragen.
- **Preise:** direkt von der Anbieterseite, nicht aus Vergleichsportalen. Boni werden nicht berücksichtigt.
- **PLZ-Belieferung:**
  - `ja`: Preise erscheinen nach Eingabe der PLZ (der generische Adapter prüft die PLZ nicht gegen eine Liefergebietsliste).
  - `nein`: Die Seite meldet ausdrücklich, dass nicht geliefert wird.
  - `unbekannt`: weder Preise noch eindeutige Absage.

## Anbieter-Adapter

Viele Tarifrechner funktionieren mit dem generischen Ablauf. Anbieter mit eigenem Ablauf bekommen einen Adapter:

```python
# app/checker.py
async def mein_anbieter_check(browser, url, plz, kwh) -> dict:
    ...  # eigene Schritte
    return {"tariffs": [...], "plz_ok": True|False|None, "form": {}, "note": "..."}

ADAPTERS["slug-aus-anbieter-details.csv"] = mein_anbieter_check
```

Beispiel: `vattenfall_check` übergibt PLZ und Verbrauch als URL-Parameter, weil das Verbrauchsfeld schreibgeschützt ist.

## Bekannte Grenzen

- **Abdeckung:** Die meisten der 1.387 Anbieter haben keine ermittelte Website. Die Zeile erscheint, aber ohne Preis.
- **Generische Textauswertung:** Preise werden aus dem Seitentext gepaart (Arbeitspreis mit der nächstgelegenen Grundgebühr). Das kann bei unübersichtlichen Seiten falsch sein. Der Hinweis unter jeder Zeile zeigt die Textstelle, damit sich das prüfen lässt.
- **Preise hängen vom Verbrauch ab:** Der Cache-Schlüssel enthält deshalb PLZ und kWh.
- **Anbieter-Änderungen:** Formulare ändern sich. Dann muss der Adapter angepasst werden.
- **Dauer:** Eine vollständige Abfrage mit ~100 Websites dauert mehrere Minuten. Ergebnisse werden 12 h gecacht.
- **Keine Rechts- oder Tarifberatung:** Die Ergebnisse sind Richtwerte und ersetzen nicht die Prüfung beim Anbieter.

## Lizenz / Datenschutz

Die App speichert nur Tarifdaten im Cache (`data/cache.json`), keine Nutzerdaten.
