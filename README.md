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

**Programm herunterladen / aktualisieren:** Doppelklick auf **`update.bat`**.

Dieses eine Skript genügt für alles rund um den Programmcode: Beim allerersten
Mal lädt es die komplette App herunter (kein GitHub-Konto, keine
Git-Kenntnisse nötig — falls Git auf dem Computer fehlt, wird automatisch
eine kleine, eigene Kopie eingerichtet, ohne Installation oder
Administratorrechte). Bei jedem weiteren Doppelklick prüft es auf eine
neuere Version und holt diese bei Bedarf nach. Ihre eigenen Daten (Ordner
`data`, `output`, `backups`) werden dabei nie verändert.

**Start:** Doppelklick auf **`start.bat`**.

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
- **Personen** — Personen/Firmen, die an der LEG teilnehmen, inkl.
  Detailansicht mit allen ihnen zugeordneten Messpunkten.
- **Messpunkte** — Messpunkte des Netzbetreibers (Bezug oder Einspeisung),
  jeder fix an einem Standort.
- **Standorte** — physische Netzanschlusspunkte (Adresse, Lage, LEG). Die
  LEG wird manuell zugewiesen: im Dropdown genügt es, einen Teil der
  Bezeichnung einzutippen, um passende LEGs zu finden.
- **LEGs** — lokale Elektrizitätsgemeinschaften, eine Eigenschaft des
  Standorts (nie einer Person oder eines Messpunkts direkt). Per Default
  entspricht eine LEG genau einem physischen Trafokreis der BKW — sie kann
  aber auch gezielt mehrere Trafokreise zusammenfassen, wenn sich Personen
  aus verschiedenen Trafokreisen zu einer gemeinsamen Abrechnung
  zusammenschliessen. Der LEG-Name erscheint als Absender auf den
  Rechnungen dieser LEG. Name (muss eindeutig sein) und optionale
  Bemerkung.
- **Zuordnungen** — welcher Messpunkt in welchem Zeitraum zu welcher
  Person gehört (wichtig bei Umzügen mitten im Quartal).
- **Import** — Messdaten der BKW einlesen (EBIX-XML oder CSV).
- **Abrechnung** — für eine LEG und ein Quartal je Person eine kombinierte
  Abrechnung berechnen und als PDF erzeugen (siehe unten). Jede LEG wird
  unabhängig abgerechnet.
- **Auswertungen** — je LEG gewählt: Übersicht je Person sowie
  Plausibilitätsprüfungen (fehlende Zuordnungen, Lücken in Messdaten,
  Standorte ohne LEG, Summenabgleich).
- **Einstellungen** — Absender-Adresse/QR-IBAN, interner Strompreis,
  Verwaltungsaufwand (Rp./kWh) und Kosten der Papierrechnung, sowie der
  Demo-Daten-Generator zum Ausprobieren. Diese Werte gelten global für alle
  LEGs; nur der Name (das Absender-Label auf der Rechnung) wird pro LEG
  festgelegt.
- **Backup** — Datenbank sichern und wiederherstellen.

Jede Listenansicht hat oben ein Suchfeld (einfacher Teilstring-Filter über
die relevanten Spalten, inkl. verknüpfter Daten wie Messpunkt oder
Standort-Adresse).

**Zum Ausprobieren:** Unter „Einstellungen" den Knopf **„Demo-Daten
erzeugen"** anklicken. Das legt eine Beispiel-LEG mit Standorten, fünf
Beispiel-Personen mit Messpunkten, Zuordnungen und einem Jahr
synthetischer Messdaten an, inklusive eines Beispiel-Umzugs. Damit lässt
sich direkt eine vollständige Test-Abrechnung inkl. PDF-Rechnungen
durchklicken.

---

## 3. Ihre eigenen Daten eingeben

Bevor Sie echte Rechnungen erzeugen:

1. **Einstellungen:** Absender-Adresse und **QR-IBAN** eintragen, sowie den
   internen Strompreis (Startwert 12 Rp./kWh) und ggf. Verwaltungsaufwand
   und Kosten der Papierrechnung.
2. **LEGs** und **Standorte** anlegen (LEG-Zuweisung am Standort erfolgt
   manuell über ein durchsuchbares Dropdown). Der LEG-Name wird als
   Absender auf den Rechnungen dieser LEG gedruckt — im Normalfall die
   BKW-Bezeichnung des Trafokreises.
3. **Messpunkte** anlegen, mit der Messpunkt-Bezeichnung aus den
   BKW-Unterlagen, der korrekten Messrichtung (Bezug / Einspeisung) und
   dem zugehörigen Standort.
4. **Personen** anlegen (Anrede, Name, Kontakt, Rechnungsadresse, IBAN für
   Gutschriften, ob Papierrechnung gewünscht ist). Die Kundennummer wird
   beim Anlegen automatisch und zufällig vergeben (keine fortlaufende
   Nummer, um Rückschlüsse auf Kundenanzahl oder -reihenfolge zu
   verhindern) und bleibt danach unveränderlich.
5. **Zuordnungen** anlegen: welche Person welchen Messpunkt ab welchem
   Datum nutzt. Bei einem Mieterwechsel zwei Zuordnungen mit passendem
   End-/Startdatum anlegen — die App teilt die Messwerte dann automatisch
   korrekt zwischen den beiden Personen auf.
6. **Import:** die von der BKW gelieferte Datei hochladen.
7. **Abrechnung:** LEG, Jahr und Quartal wählen, Abrechnung erstellen,
   PDFs erzeugen. Jede LEG wird unabhängig und pro Quartal separat
   abgerechnet.

### Ein PDF pro Person

Jede Person erhält für ein Quartal genau **ein** PDF, unabhängig davon, ob
sie nur Strom bezieht, nur einspeist, oder beides tut (Prosumer):

- Persönliche Anrede (Anrede + Name) und die Kundennummer der Person
  (gruppiert dargestellt, z. B. `80 083 138`).
- Zuerst der **Bezug** als Quartals-Summenzeile (kWh × Preis), danach die
  **Vergütung** (Einspeisung) ebenfalls als Quartals-Summenzeile.
- Sind Verwaltungsaufwand (nur auf den Bezug) und/oder die
  Papierrechnungs-Pauschale (nur falls die Person das gewählt hat) für
  diese Person ungleich null, folgt ein eigener Abschnitt mit diesen
  Positionen.
- Erst am Schluss werden Bezug, Vergütung und allfällige Gebühren
  verrechnet — **nur dort wird gerundet** (auf den Rappen genau), alle
  Zwischenwerte sind ungerundete Anzeigewerte.
- Jedes PDF enthält einen Schweizer QR-Einzahlungsschein mit Zahlungsfrist
  (45 Tage). Muss die Person der LEG Geld schulden (Netto-Betrag positiv),
  ist er normal benutzbar. Schuldet umgekehrt die LEG der Person Geld
  (Netto-Betrag negativ), wird der Betrag auf dem Einzahlungsschein durch
  `***.**` ersetzt und ist damit absichtlich **nicht als Zahlungsmittel
  nutzbar** — die Auszahlung erfolgt stattdessen durch den Verwalter über
  die Zahlliste.

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
- `output/` — erzeugte Abrechnungen (PDFs je Person), Zahllisten
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
  Plausibilitätsprüfungen inkl. LEG-Zuschnitt-Check (`quality_checks.py`),
  Demo-Daten (`demo_data.py`).
- **Import:** `app/importers/` — EBIX- und CSV-Parser, klar getrennt vom
  Rest der App.
- **PDF/QR-Rechnung:** `app/pdf/` (Bibliotheken `qrbill` + `reportlab` +
  `svglib`).
- **Persistenz:** `app/models/` — ein Modul pro Tabelle
  (`leg.py`, `standort.py`, `messpunkt.py`, `person.py`,
  `zuordnung.py`), reine CRUD-Funktionen ohne Geschäftslogik.
- **Oberfläche:** `app/gui/pages/` — ein Modul pro Seite.
- **Backup:** `app/backup/`.

Modultrennung ist bewusst strikt: die Domänenlogik kennt weder SQL noch
NiceGUI-Details, die GUI kennt keine SQL-Details, der Import kennt nichts
über das Dateiformat hinaus die Schnittstelle `ParsedReading`.
