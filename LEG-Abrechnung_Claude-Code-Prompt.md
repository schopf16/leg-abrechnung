# Projektauftrag: Abrechnungssoftware für eine Lokale Elektrizitätsgemeinschaft (LEG)

Baue eine **lokale Desktop-Anwendung**, mit der der Verwalter einer Schweizer LEG (Lokale
Elektrizitätsgemeinschaft, BKW-Netzgebiet) die Stromzählerdaten importiert, den lokal geteilten
Solarstrom viertelstündlich verteilt und daraus quartalsweise **Rechnungen und Gutschriften** als
PDF mit Schweizer QR-Rechnung erzeugt.

Die App wird von einer einzelnen, **nicht technisch versierten** Person bedient (kein
Konsolen- oder Datenbankwissen vorausgesetzt). Ein Doppelklick startet die App, es öffnet sich
eine aufgeräumte, ansprechende Oberfläche.

---

## 1. Technischer Rahmen (fix vorgegeben)

- **Sprache:** Python (aktuelle stabile Version)
- **Oberfläche:** **NiceGUI** — reine Python-GUI, läuft als eigenständiges Desktop-Fenster.
  Kein separater Server, den der Nutzer manuell starten muss.
- **Datenbank:** **SQLite**, lokal, läuft unsichtbar im Hintergrund. Keine DB-Kenntnisse nötig.
- **PDF/QR-Rechnung:** Erzeuge gültige Schweizer QR-Rechnungen (Swiss Payment Standards / ISO 20022).
  Prüfe und verwende eine aktuelle, gepflegte Bibliothek (z. B. `qrbill`); kläre Aktualität selbst ab.
- Alles läuft **offline und lokal**. Es werden personenbezogene Verbrauchsdaten verarbeitet —
  keine Cloud, keine externen Aufrufe ausser dem, was für die reine App-Funktion nötig ist.
- Start per einfachem Skript / Doppelklick. Lege eine kurze, laienverständliche README bei
  (Installation, Start, Backup der Datenbankdatei).

---

## 2. Fachlicher Kontext (damit die Logik stimmt)

Eine LEG teilt lokal produzierten Solarstrom über das öffentliche Netz. Der Verwalter erhält von
der BKW je Zähler die Messwerte als **15-Minuten-Lastgang** (Zeitreihe). Jeder Teilnehmer kann
**mehrere Zähler** haben, z. B. einen Bezugszähler und einen Produktionszähler (Prosumer), oder
zwei Bezugszähler (z. B. fix und geschaltet für eine Wärmepumpe).

**Wichtig — Abgrenzung des Auftrags:** Diese Software rechnet **ausschliesslich den internen
LEG-Strom** ab (was innerhalb der Gemeinschaft geteilt wurde). Reststrombezug und Rückspeisung
ins Netz werden direkt von der BKW abgerechnet und sind **nicht** Teil dieser Software.

---

## 3. Datenmodell (alles in der App pflegbar)

Der Nutzer muss **alle** folgenden Stammdaten über die Oberfläche anlegen und bearbeiten können.

**LEG-Einstellungen** (einmalig, gilt für alle Rechnungen):
- Name/Bezeichnung der LEG
- Absender-Adresse (Zahlungsempfänger auf den Rechnungen)
- QR-IBAN der LEG
- Interner Strompreis in **Rappen pro kWh** (Startwert: **12 Rp./kWh**), jederzeit änderbar

**Teilnehmer:**
- Name / Firma, Adresse (Strasse, PLZ, Ort, Land)
- Bankverbindung (IBAN — für Gutschriften/Auszahlungen)
- optional: E-Mail

**Zähler:**
- Zählpunkt-ID (Metering-Point-ID aus den BKW-Daten)
- Bezeichnung, zugehöriges Wohnhaus/Adresse
- **Rolle:** Bezug / Produktion / Bezug-fix / Bezug-geschaltet
- Zähler sind **fix pro Wohnhaus** und ändern in der Regel nicht.

**Zuordnung Zähler → Teilnehmer (zeitlich begrenzt — zentral für Umzüge):**
- Jeder Zähler hat eine **Historie** von Zuordnungen: Teilnehmer, `gültig_von` (Datum),
  `gültig_bis` (Datum **oder** unbestimmt/offen).
- So werden **Umzüge während eines Quartals** korrekt berücksichtigt: Jeder 15-Min-Wert wird bei
  der Abrechnung demjenigen Teilnehmer zugeschrieben, der zum Zeitpunkt des Werts zugeordnet war.
  Ein Zähler mit Mieterwechsel mitten im Quartal wird also automatisch auf zwei Teilnehmer aufgeteilt.
- Die Oberfläche muss überlappende/lückenhafte Zuordnungen erkennen und warnen.

**Messwerte:** 15-Min-Zeitreihe je Zähler (Zeitstempel, kWh, Richtung), importiert aus EBIX.

**Abrechnungsläufe & Dokumente:** je Quartal ein Lauf mit erzeugten PDFs und einer Zahlliste.

Erwartete Grösse: **10–30 Teilnehmer**, ~1–2 Zähler je Teilnehmer, 15-Min-Werte
(~35'000 Werte pro Zähler und Jahr). Auf robuste, zügige Verarbeitung dieser Datenmengen achten.

---

## 4. EBIX-Import (offener Punkt — bitte defensiv und austauschbar bauen)

Die BKW liefert die Messdaten im **EBIX-Format** (XML-basiert, Schweizer Ausprägung SDAT-CH,
Versand über den Datahub swisseldex). Die Zeitreihen sind je Zählpunkt abgelegt, Messwerte über
OBIS-Kennzahlen und Richtung (Bezug/Produktion) identifiziert.

**Achtung:** Aktuell liegt **noch keine echte Beispieldatei** vor. Daher:
- Kapsle das Parsen in ein **eigenes, klar isoliertes Modul** mit sauberer Schnittstelle
  (rein → validierte Messwerte). Der Rest der App darf nichts über das Dateiformat wissen.
- Baue den Parser gegen die ebIX/SDAT-CH-Struktur, aber so, dass das **Anpassen an das reale
  Schema ein kleiner, klar dokumentierter Eingriff** ist. Markiere diese Stelle deutlich.
- Lege **Test-Fixtures** (Beispiel-XML nach ebIX-Struktur) an, die der Nutzer später durch echte
  Dateien ersetzt. Schreibe Tests gegen diese Fixtures.
- Unterstütze zusätzlich den **CSV-Import** (die BKW bietet die Daten alternativ als CSV an) —
  das ist als Fallback wertvoll, solange EBIX noch nicht final ist.
- Import muss idempotent sein (mehrfaches Einlesen derselben Periode darf nichts doppeln) und
  Zählpunkt-IDs den in der App gepflegten Zählern zuordnen; unbekannte IDs klar melden.

---

## 5. Verteil- und Abrechnungslogik (der Kern — bitte exakt so)

Verteilung des lokalen Solarstroms **proportional zum Verbrauch, je 15-Minuten-Intervall**.

Für jedes 15-Min-Intervall *t* im Abrechnungsquartal:
1. Gesamte LEG-Produktion `P(t)` = Summe aller Produktionszähler-Werte in *t*.
2. Gesamter LEG-Verbrauch `C(t)` = Summe aller Bezugszähler-Werte in *t*.
3. Lokal geteilte Energie `S(t) = min(P(t), C(t))` (nur zeitgleich Produziertes kann geteilt werden).
4. Je Bezugszähler *m*: lokal gedeckter Anteil = `verbrauch_m(t) × S(t) / C(t)` (0, falls `C(t)=0`).
5. Je Produktionszähler *m*: lokal gelieferter Anteil = `produktion_m(t) × S(t) / P(t)` (0, falls `P(t)=0`).

Danach:
6. Jeden Zähler-Intervallwert dem zum Zeitpunkt *t* **gültig zugeordneten Teilnehmer** zuschreiben.
7. Pro Teilnehmer über das Quartal summieren: bezogene LEG-kWh und gelieferte LEG-kWh.
8. **Rechnung** (Bezüger): bezogene LEG-kWh × Preis. **Gutschrift** (Produzent): gelieferte LEG-kWh × Preis.

Hinweise:
- **Prosumer** (Bezug *und* Produktion) erhalten **getrennt**: eine Rechnung für den Bezug **und**
  eine Gutschrift für die Produktion (nicht netto verrechnen).
- Bei einheitlichem Preis muss die Summe aller Rechnungen der Summe aller Gutschriften entsprechen —
  baue eine **Kontrollprüfung** ein, die das verifiziert (Rundungsdifferenzen sauber behandeln).
- Interne kWh mit ausreichender Präzision (z. B. 3 Nachkommastellen), Endbeträge in CHF nach
  Schweizer Regeln runden (Rappenrundung, wo für Zahlbeträge sinnvoll).
- **Keine MWST** auf den Rechnungen/Gutschriften.

---

## 6. Ausgaben

- **Rechnungen** an Bezüger: PDF mit gültiger **Schweizer QR-Rechnung** (QR-IBAN & Absender aus den
  LEG-Einstellungen, Empfänger = Teilnehmer). Klare Positionen: Menge (kWh), Preis, Betrag, Periode.
- **Gutschriften** an Produzenten: PDF-Beleg mit Menge, Preis, Betrag, Periode und Teilnehmer-IBAN.
- **Zahlliste** für die Auszahlungen an Produzenten (Übersicht: Teilnehmer, IBAN, Betrag) — der
  Verwalter überweist selbst; die Software liefert Beleg + Liste.
- Alle Dokumente eines Laufs gesammelt exportierbar (Ordner je Quartal).

## 7. Auswertungen & Qualität

- Übersicht je Quartal und je Teilnehmer (geteilte kWh, Beträge), Plausibilitäts-/Kontrollprüfungen
  (fehlende Zeiträume, Lücken in Zuordnungen, Summenabgleich Rechnungen ↔ Gutschriften).
- **Automatisierte Tests**, insbesondere für (a) die 15-Min-Verteilung inkl. `min(P,C)`-Fällen,
  (b) die zeitliche Zuordnung bei Umzügen (Aufteilung eines Zählers auf zwei Teilnehmer),
  (c) den Summenabgleich, (d) den Import (idempotent, unbekannte Zählpunkte).
- Sauberer, dokumentierter, wartbarer Code; klare Modultrennung (Import / Domänenlogik / GUI / PDF).

---

## 8. Backup & Datenbank-Migration

**Backup (manuell, selbsterklärend):**
- Knopf „Backup erstellen" → schreibt einen **Dump der gesamten Datenbank** als einzelne, mit
  Zeitstempel benannte Datei in einen `backups/`-Ordner. Bewusst simpel: eine Datei = ein Backup.
- Knopf „Backup wiederherstellen" → Nutzer wählt eine Backup-Datei; die App **ersetzt die aktuelle
  Datenbank vollständig** durch den Inhalt des Backups (aktuelle DB löschen, aus Backup neu aufbauen).
- Vor dem Wiederherstellen **automatisch ein Sicherheits-Backup** der aktuellen DB anlegen und den
  Nutzer klar bestätigen lassen (Warnung, dass die aktuellen Daten überschrieben werden).

**Datenbank-Versionierung & Migration (wichtig):**
- Die Datenbank trägt eine **Schema-Versionsnummer** (in einer Meta-Tabelle).
- Beim Start **und** beim Wiederherstellen prüft die App die Version. Ist ein Backup **älter** als das
  aktuelle Schema, werden **Migrationen** ausgeführt, die es Schritt für Schritt auf den aktuellen Stand
  heben — so bleiben alte Backups auch nach Weiterentwicklung der App ladbar.
- Nutze einen **einfachen, expliziten Migrationsmechanismus** (nummerierte Migrationsschritte); jede
  Schemaänderung erhöht die Version und bekommt eine Migration. Kurz in der README erklärt.
- Der `backups/`-Ordner enthält echte Daten und gehört **in die `.gitignore`** (siehe Abschnitt 9).

---

## 9. Code-Konventionen, Tests & Versionierung

**Code-Konventionen:**
- Der gesamte **Quellcode ist auf Englisch** — Bezeichner (Variablen, Funktionen, Klassen),
  Kommentare und Commit-Messages. **Ausnahme:** alle für den Nutzer sichtbaren Texte
  (GUI-Beschriftungen, Meldungen, Rechnungs- und Gutschrifttexte) bleiben **auf Deutsch**.
- **Jede Funktion und Methode erhält einen Docstring** (englisch), der beschreibt: erwartete
  Parameter (inkl. Typ/Einheit), was die Funktion tut, und was sie zurückgibt.
- Code **kurz und wartbar** halten: gemeinsame Logik in gut benannte Hilfsfunktionen auslagern
  statt zu wiederholen. **Keine komplexen oder verschachtelten Lambda-Ausdrücke** — bevorzuge
  normale, benannte Funktionen mit klaren Namen (Lesbarkeit vor Kürze). Kleine, fokussierte Funktionen.
- Sprechende Namen, klare Modulgrenzen (Import / Domänenlogik / GUI / PDF / Persistenz).

**Tests (parallel, immer):**
- Zu **jeder** neuen Funktion und Änderung werden **zeitgleich Unit-Tests** geschrieben — nicht erst
  am Schluss. Ziel ist eine **Regressions-Suite**, die künftige Änderungen absichert.
- Priorität hat die Fachlogik mit ihren Randfällen: 15-Min-Verteilung inkl. `C(t)=0`/`P(t)=0`,
  Zuordnung bei Umzügen (Zähler auf zwei Teilnehmer aufgeteilt), Summenabgleich Rechnungen ↔
  Gutschriften, idempotenter Import und unbekannte Zählpunkte.
- Gängiges Framework (z. B. `pytest`), Ausführung per einfachem Befehl; in der README kurz erklärt.

**Demo-/Testdaten (zum Ausprobieren und für Tests):**
- Ein Skript bzw. eine Funktion legt **vier Beispiel-Teilnehmer** an: zwei **Prosumer**
  (Bezug + Produktion) und zwei reine **Bezüger**, inkl. Zähler und zeitlichen Zuordnungen.
- Erzeuge dazu **synthetische 15-Min-Messwerte** über mehrere Quartale, die gezielt die Randfälle treffen:
  - **Dezember-Quartal (Winter):** niemand speist lokal ein (Produktion ≈ 0) → keine LEG-Teilung,
    LEG-Beträge = 0; testet den `P(t)=0`-Fall.
  - **Sommer-Quartal:** zeitweise mehr Produktion als Verbrauch → Überschuss wird **nicht** vollständig
    lokal genutzt (`S(t)=min(P,C)=C`); daneben Intervalle mit `P<C`. Testet den durch den Verbrauch
    begrenzten Fall und den korrekten Ausschluss des Überschusses.
- **Zusätzlich empfohlen:** ein **Umzug** mitten im Quartal (Zuordnungswechsel eines Zählers), damit die
  zeitliche Aufteilung auf zwei Teilnehmer mitgetestet wird.
- Diese Demo-Daten dienen doppelt: zum Durchklicken der App und als Basis für die Unit-Tests der Engine.

**Versionierung auf GitHub (öffentlich) & Datensicherheit:**
- Der Code wird in einem **öffentlichen** GitHub-Repo gesichert. Deshalb dürfen **niemals sensible
  Daten** hineingelangen: die SQLite-Datenbank, erzeugte PDFs/Zahllisten sowie jede Konfiguration
  mit QR-IBAN, Adressen oder Bankverbindungen (die App verarbeitet Personendaten und IBANs).
- Lege eine **strikte `.gitignore`** an, die zuverlässig ausschliesst: Datenbankdatei(en), das
  Datenverzeichnis (`data/`), das Ausgabeverzeichnis (`output/`), das Backup-Verzeichnis (`backups/`),
  lokale Konfig-/Secret-Dateien
  (z. B. `.env`, lokale `config.*`), `__pycache__/`, virtuelle Umgebungen usw.
- **Trenne strikt Code und Daten:** alle sensiblen Inhalte leben in gitignorierten Ordnern
  (`data/`, `output/`), niemals hartcodiert im Code. Committe stattdessen eine **Beispiel-/Leer-Konfig**
  (`config.example.*`) ohne echte Werte.
- Lege ein kleines **Windows-Helfer-Skript (`.bat`)** bei, das die Standard-Git-Schritte
  (`add`/`commit`/`push`) nur für den Code ausführt und vorab einen **Sicherheits-Check** macht:
  warnen/abbrechen, falls versehentlich eine Datenbank- oder Konfig-Datei im Commit landen würde.
- Kurzer, laienverständlicher README-Abschnitt: wie das Repo initial aufgesetzt und wie gesichert wird.

---

## 10. Vorgehen (bitte schrittweise, jeweils lauffähig)

1. Projektgerüst, SQLite-Schema **inkl. Schema-Version und Migrationsgerüst**, NiceGUI-Grundfenster mit
   Navigation; **Git-Repo mit `.gitignore`** und Ordnertrennung Code / `data/` / `output/` / `backups/`.
2. Stammdaten-Verwaltung: Teilnehmer, Zähler, **zeitliche Zuordnungen**, LEG-Einstellungen (CRUD, mit
   Validierung); **Demo-Daten-Generator** (die vier Testpersonen).
3. Import-Modul (EBIX defensiv + CSV-Fallback) inkl. Test-Fixtures.
4. Verteil-/Abrechnungsengine gemäss Abschnitt 5, mit Tests.
5. PDF-Erzeugung (QR-Rechnung + Gutschrift) und Zahlliste.
6. **Backup/Wiederherstellung inkl. Migration** (Abschnitt 8).
7. Auswertungen, Kontrollprüfungen, Feinschliff der Oberfläche, README.

Nach jedem Schritt kurz zeigen, was läuft, bevor es weitergeht.

---

## 11. Vom Nutzer noch nachzuliefern (Platzhalter im Code klar markieren)

- **Echte EBIX-Beispieldatei** der BKW → zum finalen Anpassen des Parsers (Abschnitt 4).
  Bis dahin gegen die Fixtures entwickeln.
- QR-IBAN, Absender-Adresse und der genaue Preis werden **in der App** eingegeben (nicht hartcodieren);
  Startwert Preis = 12 Rp./kWh.
