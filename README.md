# THW OV Passau – Küchenlogistik (Mehrbenutzer-Version)

Webapp mit FastAPI, PostgreSQL, Admin-Benutzerverwaltung, zentralen Standardmaterialien,
Veranstaltungen, Checklisten, Mengenerfassung, Ausgabedokumentation und Änderungsprotokoll.

## Schnellstart

Voraussetzung: Docker Engine / Docker Desktop mit Docker Compose V2.

```bash
cp .env.example .env
# .env bearbeiten und beide Passwörter durch starke, unterschiedliche Passwörter ersetzen
# ADMIN_PASSWORD mindestens 12 Zeichen
docker compose up -d --build
docker compose ps
```

Die Website ist danach unter **http://localhost:8080** erreichbar. Anmeldung mit
`ADMIN_USERNAME` und `ADMIN_PASSWORD` aus `.env` beim **ersten Start**.
Danach das Adminpasswort bei Bedarf unter **Benutzer** ändern: Änderungen an der
`.env` ersetzen kein bestehendes Benutzerpasswort.

## Bedienung

- Admin: Standardausstattung (Kategorien, Artikel, aktiv/inaktiv, Reihenfolge) sowie Benutzer pflegen.
- Benutzer: Termine anlegen, Material abhaken, Mengen/Notizen und Ausgabedokumentation speichern.
- Jeder neue Termin erhält eine **Kopie** der zu diesem Zeitpunkt aktiven Standardartikel.
  Änderungen an Vorlagen wirken **nicht rückwirkend** auf bestehende Termine.
- Nur Admins können Termine löschen. Benutzer können eigene Zusatzpunkte zu Terminen ergänzen.
- Alle Benutzer mit aktivem Account können alle Veranstaltungen sehen und bearbeiten.
- JSON-Export erlaubt Auswertung/Archivierung, ist **kein** Datenbank-Restore.

## Auf Server / mit Domain hosten

Standardmäßig bindet der Webcontainer nur an `127.0.0.1:8080`.
Einen Reverse Proxy (z. B. Caddy, Traefik oder Nginx) mit **HTTPS** davorschalten,
`COOKIE_SECURE=true` in `.env` setzen und per `docker compose up -d` neu starten.
HTTP sollte nicht öffentlich angeboten werden. Reverse Proxy an `127.0.0.1:8080` weiterleiten.
Für fremde Proxies ggf. Uvicorn `--forwarded-allow-ips` gezielt anpassen.

### Backups

Regelmäßige Backups sind zwingend zu organisieren, z. B.:

```bash
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > thwkueche_$(date +%F).dump
```

Sicher und verschlüsselt außerhalb des Servers speichern. Rücksicherung vorher
in einer Testumgebung prüfen, z. B. mit `pg_restore` (Überschreiben nur mit
geplantem Wartungsfenster). Volume `postgres_data` darf nicht gelöscht werden.

## Architektur / Schnittstellen

- Frontend: Astro mit separaten Komponenten, gemeinsamem Layout und clientseitigem JavaScript; statischer Build vom Backend über `/` und `/assets/*` ausgeliefert.
- Neue Termine werden ausschließlich in der Veranstaltungsübersicht angelegt.
- Backend: FastAPI mit JSON-API unter `/api/*`.
- DB: PostgreSQL 17 im eigenen Container, persistentes Docker-Volume.
- Authentifizierung: Argon2id-Passwort-Hash; zufällige, **gehashte** serverseitige
  Sitzungs-IDs; HttpOnly/SameSite-Cookie, CSRF-Header für Schreiboperationen.
- Adminzugriff wird serverseitig geprüft. Deaktivierte Benutzer verlieren Zugriff.
- Audit: Benutzername, Zeitpunkt und Aktion (ohne vorher/nachher Inhalt).
- Container: non-root, read-only App-Filesystem, DB nicht nach außen freigegeben.

API-Routen: `/api/auth/login`, `/api/auth/me`, `/api/auth/logout`,
`/api/users`, `/api/categories`, `/api/templates`, `/api/events`,
`/api/events/{id}/items`, `/api/events/{id}/documentation`, `/api/audit`, `/api/health`.

## Stand / Grenzen der ersten Serverversion

- Backend-Tabellen werden beim Start automatisch angelegt. **Noch kein
  Migrationstool (Alembic)**; für größere Versionsupdates vor dem Deployment
  Migrationsstrategie ergänzen und ein Backup machen.
- Keine Passwort-zurücksetzen-per-E-Mail-Funktion; Admin kann Passwörter setzen.
- Keine automatische Mengenplanung oder Lagerbestandsbuchungen.
- Keine Dateianhänge, nur Belegnummern/Notizen.
- Gleichzeitige Änderungen *am selben Feld* folgen „zuletzt gespeichert gewinnt“.
- Login hat noch keinen Rate-Limiter; bei Veröffentlichung im Internet unbedingt
  Proxy-Rate-Limits / Fail2ban bzw. vergleichbare Schutzmaßnahmen einrichten.
- Der Adminbootstrap gilt nur für eine **leere** Datenbank. Ist kein Admin mehr
  erreichbar, ist ein manueller Recovery-Prozess nötig.
- Für professionelle Produktion zusätzlich TLS, Monitoring, Backup-Restore-Tests,
  automatisierte Sicherheitsupdates und Berechtigungskonzept prüfen.

Diese Anwendung ist keine offizielle THW-Anwendung.

## Frontend entwickeln

Voraussetzung: Node.js 22 und ein laufendes Backend auf Port 8000.

```bash
cd frontend
npm ci
npm run dev
```

Astro stellt die Oberfläche unter http://localhost:4321 bereit und leitet
`/api` an das lokale Backend weiter. Layout und globale Styles liegen unter
`frontend/src/layouts` und `frontend/src/styles`; die einzelnen Ansichten,
Navigation und Dialoge unter `frontend/src/components`. Die clientseitige
Interaktion liegt unter `frontend/src/scripts/app.js`.

`npm run build` erzeugt `frontend/dist`. Diesen Build vor dem direkten Start
von FastAPI erstellen. Docker baut das Frontend automatisch in einer separaten
Node-Buildstufe; im laufenden Webcontainer wird kein Node.js benötigt.
