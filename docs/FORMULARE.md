# Originalformulare und Versionierung

Die acht Dateien unter `backend/forms/Spez71-02.pdf` bis `Spez71-09.pdf` sind
unveränderte vom Nutzer bereitgestellte Originale (April 2020). Die dazugehörigen
JSON-Dateien enthalten Feldpositionen in PDF-Punkten, Ursprung oben links.
Die Anwendung zeichnet ausschließlich Feldinhalte auf die Originalseiten.
Bei weiteren Tabellenblättern wird außerdem nur der vorhandene Seitenzähler
rechts unten ersetzt. Keine zusätzliche Kopfzeile, Wasserzeichen oder Signatur.

Die Oberfläche zeigt das Original als Bild mit Eingabefeldern darüber. Das
exportierte PDF enthält Text/Vektoren über dem Original und ist kein Screenshot.
Unterschriften- und Handzeichenfelder sind im mitgelieferten Layout ausgespart.
Bei automatischen Messungen steht die technische Quelle im Namensfeld; dies
ersetzt keine menschliche Kontrolle oder Unterschrift.

## Eine spätere Fassung hinterlegen

Nur Administratoren können unter OV-Konfiguration eine neue PDF-Fassung mit
neuem Feldlayout hochladen. Sie wird eine neue, unveränderbare Version. Alte
Nachweise und deren PDF bleiben an ihre bisherige Version gebunden. Der
Formularcode (z. B. Spez71-06) verbindet die Versionen. Neue Versionen machen
vorherige Versionen dieses Codes für neue Nachweise inaktiv.

Beispiel für ein Layout:

```json
{
  "pages": [{
    "width": 595.32,
    "height": 841.92,
    "fields": [
      {"id":"ort", "kind":"text", "label":"Ort", "rect":[85,133,367,147]},
      {"id":"ja", "kind":"checkbox", "label":"Ja", "rect":[240,250,249,259]}
    ]
  }],
  "repeat_page": null
}
```

Jede Feld-ID muss innerhalb der Vorlage eindeutig sein. Rechtecke müssen auf der
Seite liegen. Signaturfelder dürfen nicht als Eingabefelder definiert werden.
`repeat_page` ist der nullbasierte Index des wiederholbaren Tabellenblatts oder
null. Weitere Blätter werden an die vollständige Originalvorlage angehängt.
Bei Temperaturexportvorlagen muss das Tabellenblatt Seite 0 sein; Tabellenfelder
benötigen `row` (ab 0) und `column`: 0 Datum, 1 Uhrzeit, 2 Ist-Temperatur,
3 kontrollierende Person/technische Quelle, 5 Bemerkungen. Spalte 4 bleibt frei.
Ein Kopf-Textfeld ohne `row` nimmt Gerätebezeichnung und Standort auf.

Das Hilfsprogramm `python -m backend.prepare_form ORIGINAL.pdf --code Spez71-06`
kann ein vorläufiges Layout erzeugen. Immer alle Seiten mit Original und einem
vollständig ausgefüllten Muster visuell prüfen und Feldpositionen korrigieren,
bevor eine neue Fassung freigegeben wird. Layoutgenerierung ist eine Hilfe,
keine automatische fachliche Freigabe.

## Entwurf und Finalisierung

Speichern aktualisiert einen Entwurf mit Versionsprüfung gegen konkurrierende
Änderungen. „Final Speichern“ ist nur OV-Stab und Administrator erlaubt und
friert Formularwerte, Vorlagenversion, PDF, SHA-256-Prüfsumme, Person und Zeitpunkt
ein. Datenbanktrigger verhindern spätere Änderungen. Sie sind kein Schutz gegen
einen Datenbankeigentümer mit direktem administrativem Datenbankzugriff.
Berichtigungen werden als neuer Nachweis mit Referenz auf das Original angelegt.

Leere Nachweise können nicht finalisiert werden; weitere fachliche Pflichtfelder
sind nicht pauschal erzwungen, da die Formulare situationsabhängig verwendet
werden. PDF-Felder dürfen nicht überlaufen: nicht passender Text wird mit 422
zurückgewiesen. Die mitgelieferte DejaVu-Schrift unterstützt unter anderem deutsche und weitere
europäische Zeichen; nicht darstellbare Zeichen werden zurückgewiesen.
Keine nachträgliche elektronische Signatur: Papierfelder bleiben leer.

Temperatur-Export wählt den ersten tatsächlichen Messwert pro Zeitfenster ab
gewählter Startzeit (inklusive Start und Ende). Der Entwurf zeigt an, wie viele
Grenzwertverletzungen dadurch nicht enthalten sind. Rohdaten werden nie
reduziert. Das PDF wird erst durch den separaten Finalisierungsschritt gesperrt.

Bemerkungen können beim Temperaturexport ausdrücklich abgewählt werden, wenn
langes Protokollmaterial nicht in die Felder des Originals passt. Die vollständigen
Bemerkungen bleiben bei den Rohmessungen erhalten. Es wird nichts abgeschnitten.
