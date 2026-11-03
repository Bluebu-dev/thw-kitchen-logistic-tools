# THW Küchenlogistik · Version 2

Verwaltung für einen Ortsverband: Einsätze, Übungen und Veranstaltungen,
kombinierbare Checklisten, Lebensmittel-/Getränkelager, Dateiablage,
Temperaturverläufe und Spez71-Nachweise auf den Originalformularen von April 2020.
OV-Name und Logo sind durch Administratoren konfigurierbar. Eigenständige,
nicht offizielle THW-Anwendung. Onlinebetrieb, kein Offline-Abgleich.

## Start

Python 3.12, PostgreSQL 17; empfohlen Docker Compose V2:

```sh
cp .env.example .env
# Zwei unterschiedliche zufällige Passwörter setzen (Admin mindestens 12 Zeichen).
docker compose up -d --build
docker compose ps
```

Die Anwendung bindet standardmäßig nur an 127.0.0.1:8080. Der Admin wird beim
ersten Start in einer leeren Datenbank angelegt. Änderungen von ADMIN_PASSWORD
ändern später keine Benutzerpasswörter; dafür die Benutzerverwaltung verwenden.
OV_NAME bestimmt den anfänglichen Namen, anschließend gilt die OV-Konfiguration.
Öffentlichen Zugriff ausschließlich über HTTPS-Reverse-Proxy betreiben,
COOKIE_SECURE=true setzen. Proxy-Rate-Limits für Anmeldung/API konfigurieren.

**Version 2 startet mit einer neuen Datenbank.** Eine bestehende Version-1-
Datenbank wird erkannt und nicht verändert. Wie vereinbart ist keine Migration
bestehender Benutzerdaten oder Vorgänge enthalten. Für die neue Version ein neues
Compose-Projekt (`docker compose -p kueche-v2 up -d --build`) oder eine neue leere
Datenbank verwenden. Alte Daten und Volumes nicht ungeprüft löschen. Weitere
Schemaänderungen benötigen künftig explizite Datenbankmigrationen.

### Lokale Entwicklung

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
# DATABASE_URL auf eine eigene PostgreSQL-Datenbank setzen.
# ADMIN_USERNAME und ADMIN_PASSWORD sicher über die Prozessumgebung setzen.
uvicorn backend.main:app --host 127.0.0.1 --port 8080
```

Ohne DATABASE_URL wird SQLite (`dev.db`) für lokale Entwicklung verwendet.
Produktiver Betrieb und konkurrierende Buchungen werden mit PostgreSQL getestet.
Frontend: HTML/CSS/Vanilla-JavaScript, keine zusätzliche Build-Pipeline.
Die Laufzeit benötigt Schreibzugriff nur auf die Datenbank. PDFs, Logos und
Uploads liegen zusammen mit ihren Metadaten in PostgreSQL.

## Rollen

| Funktion | Benutzer | Koch | OV-Stab | Administrator | Technischer Benutzer |
|---|---|---|---|---|---|
| Aktive Vorgänge, Bestände, Dateien sehen | ja | ja | ja | ja | nur API-Bestände mit Freigabe |
| Vorgang anlegen / Checklisten abhaken, Menge, Notiz | ja | ja | ja | ja | nein |
| Kategorien/Punkte/Vorlagen, Dokumentation und Dateien pflegen | nein | ja | ja | ja | nein |
| Lebensmittel und Geräte/Grenzwerte verwalten | nein | ja | ja | ja | nein |
| Ein-/Ausgänge buchen | nein | ja | ja | ja | mit API-Freigabe |
| Ist-Bestand durch Inventur setzen | nein | nein | ja | ja | nein |
| Temperaturen erfassen | ja | ja | ja | ja | mit API-/Gerätefreigabe |
| Bemerkung an Messung ergänzen | nein | ja | ja | ja | nein |
| Spez71-Entwürfe anlegen/ausfüllen | nein | ja | ja | ja | nein |
| Spez71 finalisieren / Temperatur-PDF erstellen | nein | nein | ja | ja | nein |
| Archiv sehen / Vorgang archivieren | nein | nein | nein | ja | nein |
| Benutzer, API-Schlüssel, OV-Logo, Originalfassungen verwalten | nein | nein | nein | ja | nein |

Ein Benutzer hat jeweils eine Rolle; mehrere Personen können dieselbe Rolle
haben. Administratoren können finalisierte Nachweise nicht ändern.

## Lager und Temperaturen

Mengen verwenden exakte Dezimalarithmetik mit maximal drei Nachkommastellen;
Stück ist ganzzahlig. Sollbestand, Ist-Bestand und farbliche Hinweise sind
getrennt vom Material der Vorgangschecklisten. Einheiten nach erster Buchung
nicht mehr ändern. Archivieren/inaktiv setzen statt Artikelhistorie entfernen.
Jede Inventur, auch ohne Differenz, erzeugt einen unveränderbaren Eintrag mit
Begründung. Negative Bestände werden verhindert; inkonsistente negative Werte
würden deutlich markiert. Chargen und Haltbarkeitsdaten sind noch nicht enthalten;
Bewegungen besitzen eigene IDs als Grundlage für die spätere Erweiterung.

Messwerte, Eingangszeit, Quelle und damals gültige Grenzen bleiben unverändert.
Neue Bemerkungen sind getrennte, ebenfalls unveränderbare Einträge. Ein
herstellerspezifischer Geräteadapter ist noch nicht enthalten. Siehe [API](docs/API.md).
Grafik/Tabelle zeigt maximal 10.000 Werte; für längere Verläufe Zeitraum eingrenzen.
Farbliche Grenzwertmarkierungen, keine automatischen Alarmnachrichten.

## Dokumente, Formulare und Aufbewahrung

PDF-Dateien: bis 5 MiB für Koch/OV-Stab, bis 50 MiB für Administratoren. Zentrale
Dateien, Vorlagendokumente und Vorgangsdokumente sind getrennt. Bei Übernahme einer
Vorlage werden ihre Dokumente als unabhängige Kopien gespeichert. Logo: PNG/JPEG
bis 5 MiB. Das Standardlogo verwendet unverändertes THW-Artwork aus den gelieferten
Originalformularen; die OV-Bezeichnung steht separat darunter. Das individuelle
OV-Logo kann in der Konfiguration hochgeladen werden.

Alle acht [Originalformulare](docs/FORMULARE.md) sind enthalten, auch mehrfach
pro Vorgang. Wareneingang, Lagerhygiene und Temperaturkontrollen sind eigenständig
nutzbar. Finalisierte PDFs werden mit Prüfsumme dauerhaft gespeichert.

„Löschen“ archiviert einen Vorgang schreibgeschützt, nur für Administratoren
sichtbar. Zehn Kalenderjahre ab dem späteren Datum aus Archivierung und letzter
Finalisierung, Schaltjahre werden berücksichtigt. Abgelaufene Vorgänge und ihre
Dateien/Nachweise werden beim Start und stündlich aus der aktiven Datenbank
entfernt. Buchungen und Temperaturrohwerte bleiben eigenständige Historien mit
numerischer Vorgangsreferenz. Nach einer Entfernung kann diese nicht mehr
geöffnet werden. Außerhalb der aktiven Datenbank müssen Betreiber insbesondere
Backups nach ihrer eigenen Aufbewahrungsrichtlinie behandeln.

## Tests

```sh
pip install -r tests/requirements.txt
python -m pytest -q
```

Standardmäßig eigene temporäre SQLite-Datenbank. PostgreSQL-Prüfung mit einer
**eigenen leeren Testdatenbank**:

```sh
TEST_DATABASE_URL='postgresql+psycopg://…/kitchen_test' python -m pytest -q
```

Tests prüfen Rollen, CSRF, Bestandsarithmetik, unveränderbare Historie,
Vorlagenkopien, Archivzugriffe/Aufbewahrung, Geräteschlüssel und die Original-PDFs.
Die Testdatenbank darf keine produktiven Daten enthalten und sollte pro Testlauf
neu sein. Browser-Smoke-Test: `pip install -r tests/requirements-browser.txt`, dann
`python tests/browser_smoke.py` gegen einen eigenen
Testserver, Zugang über TEST_USERNAME/TEST_PASSWORD in der Prozessumgebung.
System-Chromium wird automatisch verwendet; alternativ zuerst
`python -m playwright install chromium` ausführen oder TEST_CHROMIUM setzen.

## Betrieb und Sicherung

Regelmäßig PostgreSQL inklusive Dateien sichern und Rücksicherung testen:

```sh
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > kitchen.dump
```

Backups verschlüsselt außerhalb des Servers halten. Datenbank-Volume niemals
ungeplant entfernen. Keine Passwort-Recovery per E-Mail; weitere aktive
Administratorkonten vorsehen. Finale Sperren gelten für die Anwendung und ihre
Datenbanktrigger; sie ersetzen kein externes, manipulationsgeschütztes Archiv.

## Astro-Frontend entwickeln

Das Frontend verwendet Astro für Layout, gemeinsame Komponenten und den statischen
Build. Die bestehenden Ansichten von Version 2 bleiben clientseitig verfügbar.
„Vorgang anlegen“ wird ausschließlich in der Veranstaltungsübersicht für
Küchenrollen angezeigt.

Voraussetzung: Node.js 22, Backend auf Port 8000.

```bash
cd frontend
npm ci
npm run dev
```

Astro läuft unter http://localhost:4321 und leitet `/api` an das Backend weiter.
Komponenten liegen unter `frontend/src/components`, das Layout unter
`frontend/src/layouts`, Styles unter `frontend/src/styles` und die bestehende
Ansichtslogik unter `frontend/src/scripts/app.js`.

Vor einem direkten Backend-Start oder Backend-Tests `npm run build --prefix frontend`
ausführen. Das Backend liefert `frontend/dist` aus. Docker baut Astro automatisch
in einer Node-Buildstufe; der Webcontainer benötigt zur Laufzeit nur Python.
