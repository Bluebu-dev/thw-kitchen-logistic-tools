# Technische Schnittstelle (v2)

Ein Administrator legt unter „Benutzer & API“ einen aktiven technischen Benutzer
und einen API-Schlüssel an. Schlüssel nur einmal sichtbar, in der Datenbank nur
als SHA-256-Hash gespeichert. Schlüssel niemals in URL oder Quellcode ablegen.
HTTPS für entfernten Zugriff verwenden. Einzelne Rechte und optional Geräte-IDs
begrenzen den Zugang. Ein leerer Gerätefilter erlaubt alle Geräte. Widerruf oder
Deaktivierung des Benutzers wirkt sofort. Technische Benutzer können sich nicht
an der Weboberfläche anmelden.

Alle Anfragen: `Authorization: Bearer <Schlüssel>`, JSON mit UTF-8.

## Bestände

`GET /api/integration/stock/articles` benötigt `stock:read`.
Dezimalwerte werden als Dezimalzeichenketten übertragen, z. B. `"1.125"`.
Einheiten: `Stück` (ganzzahlig), `Liter`, `kg` (maximal drei Nachkommastellen).

`POST /api/integration/stock/movements` benötigt `stock:write`:

```json
{
  "article_id": 1,
  "direction": "out",
  "quantity": "0.125",
  "reason": "Verbrauch",
  "note": "Entnahme für Küche",
  "event_id": null,
  "external_id": "mein-system-buchung-00001"
}
```

`direction`: `in` oder `out`. Gründe: Zugang, Verbrauch, Verderb, Rückgabe,
Korrektur. Inventurkorrekturen sind ausschließlich dem OV-Stab/Admin über die
Webschnittstelle erlaubt. Negative Ergebnisse werden mit HTTP 409 abgelehnt.
Aktueller Bestand und Ledger werden in derselben Transaktion geändert.
Die Artikel-/Bewegungstabellen sind von Checklistenmaterial getrennt. Künftige
Chargen können an Bewegungen angebunden werden, ohne die Historie umzuschreiben.

## Temperaturmessungen

`POST /api/integration/readings` benötigt `temperature:write`:

```json
{
  "device_id": 1,
  "value": "-18.125",
  "measured_at": "2026-11-03T12:30:00+01:00",
  "event_id": null,
  "external_id": "sensor-01-messung-00001"
}
```

Die Geräte-ID wird in der Geräteverwaltung abgelesen. ISO-8601-Zeitzone ist
Pflicht, Speicherung erfolgt als Zeitpunkt. Anzeige/Export verwendet
Europe/Berlin (einschließlich Sommerzeit). Nachlieferungen sind erlaubt;
Messzeit und Eingang werden getrennt protokolliert. Maximal fünf Minuten
Uhrabweichung in die Zukunft sind erlaubt. Historische Gerätegrenzen und
Gerätebezeichnung werden mit der Messung gespeichert. Gerätewerte werden
nicht als menschliche Kontrolle oder Unterschrift ausgegeben.

## Wiederholungen und Fehler

`external_id` ist Pflicht für Maschinen. Für Lagerbuchungen muss sie je
technischem Benutzer eindeutig sein, für Temperaturen je Benutzer und Gerät.
Wiederholte identische Nutzdaten liefern denselben Datensatz ohne Doppelbuchung.
Dieselbe ID mit anderen Daten liefert 409. Bei gleichzeitig eintreffenden
identischen Anfragen kann eine davon 409 erhalten; anschließend dieselbe Anfrage
mit derselben ID erneut senden. Niemals eine neue ID für den Retry erzeugen.

HTTP 401: Schlüssel fehlt/ungültig. 403: fehlendes Recht/Gerätefreigabe.
404: Artikel/Gerät/Vorgang nicht zugänglich. 409: Konflikt, Bestand oder ID.
422: ungültige Daten. 201: erfasst oder vorhandene identische Übertragung.
Es gibt keine Änderungs- oder Löschschnittstelle für Messungen und Buchungen.

Derzeit wird kein herstellerspezifischer Sensoradapter ausgeliefert. Ein Gerät
oder Gateway muss JSON über HTTPS an diese allgemeine Schnittstelle senden.
