# LEG-Abrechnung

Desktop-Anwendung für den Verwalter einer lokalen Elektrizitätsgemeinschaft
(LEG) im BKW-Netzgebiet: Stromzählerdaten importieren, lokal geteilten
Solarstrom viertelstündlich verteilen, und daraus quartalsweise Rechnungen
und Gutschriften als PDF mit Schweizer QR-Rechnung erzeugen.

Die App läuft **vollständig offline und lokal** auf Ihrem PC. Es gibt keine
Cloud, keine externen Server und keine Internetverbindung, die für den
Betrieb nötig wäre. Alle Daten bleiben auf Ihrem Computer.

---

## 1. Installation

Vorausgesetzt wird [Python](https://www.python.org/downloads/) (Version 3.11
oder neuer) — bei der Installation die Option **"Add python.exe to PATH"**
aktivieren.

**Erster Start:** Doppelklick auf **`start.bat`**.

Das Skript richtet beim allerersten Mal automatisch eine eigene, isolierte
Python-Umgebung ein (Ordner `.venv`) und installiert alle benötigten
Programmbibliotheken. Das kann beim ersten Mal ein paar Minuten dauern.
Danach öffnet sich das Programmfenster.

Bei jedem weiteren Start genügt wieder ein Doppelklick auf `start.bat` — es
öffnet sich direkt das Fenster der App, ohne erneute Installation.

---

## 2. Bedienung

Die App ist in folgende Bereiche gegliedert (linke Navigation):

- **Übersicht** — Startseite mit Kennzahlen und ersten Schritten.
- **Teilnehmer** — Personen/Firmen, die an der LEG teilnehmen.
- **Zähler** — Stromzähler mit ihrer Rolle (Bezug, Produktion, usw.).
- **Zuordnungen** — welcher Zähler in welchem Zeitraum zu welchem
  Teilnehmer gehört (wichtig bei Umzügen mitten im Quartal).
- **Import** — Messdaten der BKW einlesen (EBIX-XML oder CSV).
- **Abrechnung** — Rechnungen/Gutschriften für ein Quartal berechnen und
  als PDF erzeugen.
- **Auswertungen** — Übersicht je Teilnehmer sowie Plausibilitätsprüfungen
  (fehlende Zuordnungen, Lücken in Messdaten, Summenabgleich).
- **Einstellungen** — Name/Adresse/QR-IBAN der LEG, interner Strompreis,
  sowie der Demo-Daten-Generator zum Ausprobieren.
- **Backup** — Datenbank sichern und wiederherstellen.

**Zum Ausprobieren:** Unter „Einstellungen" den Knopf **„Demo-Daten
erzeugen"** anklicken. Das legt vier Beispiel-Teilnehmer mit Zählern,
Zuordnungen und einem Jahr synthetischer Messdaten an, inklusive eines
Beispiel-Umzugs. Damit lässt sich direkt eine vollständige Test-Abrechnung
inkl. PDF-Rechnungen durchklicken.

---

## 3. Ihre eigenen Daten eingeben

Bevor Sie echte Rechnungen erzeugen:

1. **Einstellungen:** Name, Absender-Adresse und **QR-IBAN** der LEG
   eintragen, sowie den internen Strompreis (Startwert 12 Rp./kWh).
2. **Teilnehmer** anlegen (Name, Adresse, IBAN für Gutschriften).
3. **Zähler** anlegen, mit der Zählpunkt-ID aus den BKW-Unterlagen und der
   korrekten Rolle (Bezug / Produktion / Bezug-fix / Bezug-geschaltet).
4. **Zuordnungen** anlegen: welcher Teilnehmer welchen Zähler ab welchem
   Datum nutzt. Bei einem Mieterwechsel zwei Zuordnungen mit passendem
   End-/Startdatum anlegen — die App teilt die Messwerte dann automatisch
   korrekt zwischen den beiden Personen auf.
5. **Import:** die von der BKW gelieferte Datei hochladen.
6. **Abrechnung:** Jahr und Quartal wählen, Abrechnung erstellen, PDFs
   erzeugen.

### Hinweis zum EBIX-Import

Beim Erstellen dieser App lag noch keine echte EBIX-Beispieldatei der BKW
vor. Der Import-Parser (`app/importers/ebix_parser.py`) ist deshalb gegen
eine plausible Näherung des ebIX/SDAT-CH-Formats gebaut und **klar als
Anpassungsstelle markiert** — sobald eine echte Datei vorliegt, muss nur
diese eine Datei angepasst werden. Bis dahin funktioniert zuverlässig der
**CSV-Import** als Rückfallebene (Format siehe
`app/importers/csv_parser.py`).

---

## 4. Backup

- **„Backup erstellen"** (Seite „Backup"): schreibt die komplette Datenbank
  als eine einzelne, mit Zeitstempel benannte Datei in den Ordner
  `backups/`. Diesen Ordner regelmässig an einen sicheren, separaten Ort
  kopieren (externe Festplatte, Cloud-Speicher) — er liegt lokal und wird
  von nichts anderem automatisch gesichert.
- **„Wiederherstellen"**: ersetzt die aktuelle Datenbank vollständig durch
  den Inhalt eines gewählten Backups. Vor dem Ersetzen wird automatisch ein
  zusätzliches Sicherheits-Backup des aktuellen Zustands angelegt, damit
  auch ein versehentliches Wiederherstellen rückgängig gemacht werden kann.

**Warum alte Backups auch später noch funktionieren:** Die Datenbank trägt
eine Versionsnummer. Ändert sich das Datenbankformat in einer künftigen
Version der App, wird beim Öffnen (Start oder Wiederherstellen) automatisch
Schritt für Schritt darauf aktualisiert — auch ein Jahre altes Backup lässt
sich also mit einer neueren App-Version noch öffnen.

---

## 5. Tests ausführen

Die Kernlogik (Verteilung, Abrechnung, Import, Migrationen) ist durch
automatisierte Tests abgesichert. Zum Ausführen:

```
.venv\Scripts\python.exe -m pytest
```

(oder einfach `pytest`, wenn die virtuelle Umgebung bereits aktiviert ist).

---

## 6. Code auf GitHub sichern

Der Code liegt in einem **öffentlichen** GitHub-Repository. Damit dabei
niemals versehentlich persönliche Daten (Datenbank, PDFs, Adressen, IBANs)
mit hochgeladen werden, sind folgende Ordner in `.gitignore` eingetragen
und werden nie mitversioniert:

- `data/` — die eigentliche Datenbank
- `output/` — erzeugte Rechnungen, Gutschriften, Zahllisten
- `backups/` — Datenbank-Backups
- `.venv/` — die lokale Python-Umgebung

**Einmalig einrichten** (in der Kommandozeile im Projektordner):

```
git remote add origin https://github.com/<ihr-benutzername>/<repo-name>.git
```

**Danach zum Sichern:** Doppelklick auf **`scripts\git_sync.bat`**. Das
Skript fügt nur Code-Dateien hinzu, prüft vorab automatisch, ob versehentlich
eine Datenbank- oder Konfigurationsdatei mit dabei wäre (und bricht in
diesem Fall ab, ohne etwas zu senden), fragt nach einer Commit-Nachricht und
sendet die Änderungen an GitHub.

---

## 7. Technischer Überblick (für Entwickler)

- **Sprache/Oberfläche:** Python + [NiceGUI](https://nicegui.io) (läuft als
  eigenständiges Desktop-Fenster, kein separater Server nötig).
- **Datenbank:** SQLite (`app/db/`), Schema-Migrationen in
  `app/db/migrations.py`.
- **Domänenlogik:** `app/domain/` — 15-Minuten-Verteilung
  (`distribution.py`), Rechnungs-/Gutschriftlogik (`billing.py`),
  Plausibilitätsprüfungen (`quality_checks.py`), Demo-Daten (`demo_data.py`).
- **Import:** `app/importers/` — EBIX- und CSV-Parser, klar getrennt vom
  Rest der App.
- **PDF/QR-Rechnung:** `app/pdf/` (Bibliotheken `qrbill` + `reportlab` +
  `svglib`).
- **Persistenz:** `app/models/` — ein Modul pro Tabelle, reine
  CRUD-Funktionen ohne Geschäftslogik.
- **Oberfläche:** `app/gui/pages/` — ein Modul pro Seite.
- **Backup:** `app/backup/`.

Modultrennung ist bewusst strikt: die Domänenlogik kennt weder SQL noch
NiceGUI-Details, die GUI kennt keine SQL-Details, der Import kennt nichts
über das Dateiformat hinaus die Schnittstelle `ParsedReading`.
