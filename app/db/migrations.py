"""Explicit, numbered database migrations.

Every schema change is expressed as a new entry in ``MIGRATIONS`` with the
next consecutive version number. Migrations are plain SQL scripts executed
in order; nothing is ever edited in place, so old backups can always be
brought up to the current schema by replaying the migrations they are
missing (see :mod:`app.db.schema`).

To add a schema change: append a new ``Migration`` with
``version = last_version + 1`` and a short ``description``. Never renumber
or remove existing entries.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    """A single, immutable schema migration step.

    Attributes:
        version: Target schema version this migration brings the database
            to. Must be exactly one higher than the previous migration.
        description: Short human-readable summary, shown in logs.
        sql: One or more SQL statements (semicolon separated) applied via
            ``executescript``.
    """

    version: int
    description: str
    sql: str


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description="Initial schema: settings, participants, meters, "
        "assignments, readings, imports, billing runs.",
        sql="""
            CREATE TABLE leg_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL DEFAULT '',
                address_street TEXT NOT NULL DEFAULT '',
                address_zip TEXT NOT NULL DEFAULT '',
                address_city TEXT NOT NULL DEFAULT '',
                address_country TEXT NOT NULL DEFAULT 'CH',
                qr_iban TEXT NOT NULL DEFAULT '',
                price_rp_per_kwh REAL NOT NULL DEFAULT 12.0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address_street TEXT NOT NULL DEFAULT '',
                address_zip TEXT NOT NULL DEFAULT '',
                address_city TEXT NOT NULL DEFAULT '',
                address_country TEXT NOT NULL DEFAULT 'CH',
                iban TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE meters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metering_point_id TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL DEFAULT '',
                building_address TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL CHECK (
                    role IN ('bezug', 'produktion', 'bezug_fix', 'bezug_geschaltet')
                ),
                created_at TEXT NOT NULL
            );

            CREATE TABLE meter_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meter_id INTEGER NOT NULL REFERENCES meters(id) ON DELETE CASCADE,
                participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_assignments_meter ON meter_assignments(meter_id);
            CREATE INDEX idx_assignments_participant ON meter_assignments(participant_id);

            CREATE TABLE import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                format TEXT NOT NULL CHECK (format IN ('ebix', 'csv')),
                imported_at TEXT NOT NULL,
                period_from TEXT,
                period_to TEXT,
                row_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meter_id INTEGER NOT NULL REFERENCES meters(id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('bezug', 'produktion')),
                kwh REAL NOT NULL,
                source TEXT NOT NULL,
                import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL,
                UNIQUE (meter_id, timestamp, direction)
            );
            CREATE INDEX idx_readings_meter_ts ON readings(meter_id, timestamp);

            CREATE TABLE billing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_year INTEGER NOT NULL,
                period_quarter INTEGER NOT NULL CHECK (period_quarter BETWEEN 1 AND 4),
                created_at TEXT NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'erstellt',
                notes TEXT NOT NULL DEFAULT '',
                UNIQUE (period_year, period_quarter)
            );

            CREATE TABLE billing_run_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                billing_run_id INTEGER NOT NULL REFERENCES billing_runs(id) ON DELETE CASCADE,
                participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
                kind TEXT NOT NULL CHECK (kind IN ('rechnung', 'gutschrift')),
                kwh REAL NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                amount_rappen INTEGER NOT NULL,
                pdf_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_items_run ON billing_run_items(billing_run_id);
        """,
    ),
    Migration(
        version=2,
        description="One combined billing document per participant: "
        "billing_run_items now holds a single net (Bezug minus Vergütung) "
        "row per participant instead of separate 'rechnung'/'gutschrift' "
        "rows, so each participant receives exactly one PDF.",
        sql="""
            DROP TABLE billing_run_items;

            CREATE TABLE billing_run_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                billing_run_id INTEGER NOT NULL REFERENCES billing_runs(id) ON DELETE CASCADE,
                participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
                consumed_kwh REAL NOT NULL,
                produced_kwh REAL NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                net_amount_rappen INTEGER NOT NULL,
                pdf_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_items_run ON billing_run_items(billing_run_id);
        """,
    ),
    Migration(
        version=3,
        description="Trafokreis is a property of the Standort (site), not "
        "the person: replaces the flat participants/meters/meter_assignments "
        "model with five entities -- trafokreis, standort, messpunkt, "
        "person, zuordnung. Meter identity moves from a free-text label to "
        "messpunkt_bezeichnung (the grid operator's own id); the former "
        "4-way meter role collapses to a 2-way messrichtung (bezug/"
        "einspeisung), since the fix/geschaltet distinction was descriptive "
        "only and never affected the distribution engine. No data "
        "migration/backfill: only demo/test data existed at this point, "
        "regenerated via the demo data generator after this runs.",
        sql="""
            DROP TABLE billing_run_items;
            DROP TABLE readings;
            DROP TABLE meter_assignments;
            DROP TABLE meters;
            DROP TABLE participants;

            CREATE TABLE trafokreis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bkw_name TEXT,
                internal_code TEXT UNIQUE,
                gemeinde TEXT NOT NULL DEFAULT '',
                geometry TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE standort (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                adresse TEXT NOT NULL,
                lage TEXT NOT NULL DEFAULT '',
                geo_east REAL,
                geo_north REAL,
                trafokreis_id INTEGER REFERENCES trafokreis(id) ON DELETE SET NULL,
                netzebene TEXT NOT NULL DEFAULT '',
                resolution_status TEXT NOT NULL DEFAULT 'unresolved' CHECK (
                    resolution_status IN ('auto', 'manual', 'unresolved')
                ),
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_standort_trafokreis ON standort(trafokreis_id);

            CREATE TABLE messpunkt (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                messpunkt_bezeichnung TEXT NOT NULL UNIQUE,
                messrichtung TEXT NOT NULL CHECK (messrichtung IN ('bezug', 'einspeisung')),
                standort_id INTEGER NOT NULL REFERENCES standort(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_messpunkt_standort ON messpunkt(standort_id);

            CREATE TABLE person (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                kontakt_email TEXT NOT NULL DEFAULT '',
                kontakt_telefon TEXT NOT NULL DEFAULT '',
                rechnungsadresse_strasse TEXT NOT NULL DEFAULT '',
                rechnungsadresse_plz TEXT NOT NULL DEFAULT '',
                rechnungsadresse_ort TEXT NOT NULL DEFAULT '',
                rechnungsadresse_land TEXT NOT NULL DEFAULT 'CH',
                iban TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE zuordnung (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
                messpunkt_id INTEGER NOT NULL REFERENCES messpunkt(id) ON DELETE CASCADE,
                gueltig_von TEXT NOT NULL,
                gueltig_bis TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_zuordnung_messpunkt ON zuordnung(messpunkt_id);
            CREATE INDEX idx_zuordnung_person ON zuordnung(person_id);

            CREATE TABLE readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                messpunkt_id INTEGER NOT NULL REFERENCES messpunkt(id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('bezug', 'einspeisung')),
                kwh REAL NOT NULL,
                source TEXT NOT NULL,
                import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL,
                UNIQUE (messpunkt_id, timestamp, direction)
            );
            CREATE INDEX idx_readings_messpunkt_ts ON readings(messpunkt_id, timestamp);

            CREATE TABLE billing_run_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                billing_run_id INTEGER NOT NULL REFERENCES billing_runs(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE RESTRICT,
                consumed_kwh REAL NOT NULL,
                produced_kwh REAL NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                net_amount_rappen INTEGER NOT NULL,
                pdf_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_items_run ON billing_run_items(billing_run_id);
        """,
    ),
    Migration(
        version=4,
        description="Simplify Trafokreis to a single unique 'name' field "
        "(replaces the bkw_name/internal_code split and drops the unused "
        "geometry field -- automatic address-based Trafokreis resolution "
        "was removed, it never had a real data source). Adds a free-text "
        "'bemerkung' field. Drops Standort.geo_east/geo_north/"
        "resolution_status, which existed only to support that removed "
        "auto-resolution feature. No data migration/backfill: only demo/"
        "test data existed at this point, regenerated via the demo data "
        "generator after this runs.",
        sql="""
            DROP TABLE billing_run_items;
            DROP TABLE readings;
            DROP TABLE zuordnung;
            DROP TABLE messpunkt;
            DROP TABLE standort;
            DROP TABLE trafokreis;

            CREATE TABLE trafokreis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                gemeinde TEXT NOT NULL DEFAULT '',
                bemerkung TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE standort (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                adresse TEXT NOT NULL,
                lage TEXT NOT NULL DEFAULT '',
                trafokreis_id INTEGER REFERENCES trafokreis(id) ON DELETE SET NULL,
                netzebene TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_standort_trafokreis ON standort(trafokreis_id);

            CREATE TABLE messpunkt (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                messpunkt_bezeichnung TEXT NOT NULL UNIQUE,
                messrichtung TEXT NOT NULL CHECK (messrichtung IN ('bezug', 'einspeisung')),
                standort_id INTEGER NOT NULL REFERENCES standort(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_messpunkt_standort ON messpunkt(standort_id);

            CREATE TABLE zuordnung (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
                messpunkt_id INTEGER NOT NULL REFERENCES messpunkt(id) ON DELETE CASCADE,
                gueltig_von TEXT NOT NULL,
                gueltig_bis TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_zuordnung_messpunkt ON zuordnung(messpunkt_id);
            CREATE INDEX idx_zuordnung_person ON zuordnung(person_id);

            CREATE TABLE readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                messpunkt_id INTEGER NOT NULL REFERENCES messpunkt(id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('bezug', 'einspeisung')),
                kwh REAL NOT NULL,
                source TEXT NOT NULL,
                import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL,
                UNIQUE (messpunkt_id, timestamp, direction)
            );
            CREATE INDEX idx_readings_messpunkt_ts ON readings(messpunkt_id, timestamp);

            CREATE TABLE billing_run_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                billing_run_id INTEGER NOT NULL REFERENCES billing_runs(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE RESTRICT,
                consumed_kwh REAL NOT NULL,
                produced_kwh REAL NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                net_amount_rappen INTEGER NOT NULL,
                pdf_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_items_run ON billing_run_items(billing_run_id);
        """,
    ),
    Migration(
        version=5,
        description="Split Standort.adresse (a single free-text string) "
        "into adresse (street), hausnummer, plz and gemeinde, so each part "
        "is individually searchable/sortable. Also constrains netzebene to "
        "the fixed set of Swiss grid levels (NE1-NE7) instead of free "
        "text. No data migration/backfill: only demo/test data existed at "
        "this point, regenerated via the demo data generator after this "
        "runs.",
        sql="""
            DROP TABLE billing_run_items;
            DROP TABLE readings;
            DROP TABLE zuordnung;
            DROP TABLE messpunkt;
            DROP TABLE standort;

            CREATE TABLE standort (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                adresse TEXT NOT NULL,
                hausnummer TEXT NOT NULL DEFAULT '',
                plz TEXT NOT NULL DEFAULT '',
                gemeinde TEXT NOT NULL DEFAULT '',
                lage TEXT NOT NULL DEFAULT '',
                trafokreis_id INTEGER REFERENCES trafokreis(id) ON DELETE SET NULL,
                netzebene TEXT NOT NULL DEFAULT 'NE7' CHECK (
                    netzebene IN ('NE1', 'NE2', 'NE3', 'NE4', 'NE5', 'NE6', 'NE7')
                ),
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_standort_trafokreis ON standort(trafokreis_id);

            CREATE TABLE messpunkt (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                messpunkt_bezeichnung TEXT NOT NULL UNIQUE,
                messrichtung TEXT NOT NULL CHECK (messrichtung IN ('bezug', 'einspeisung')),
                standort_id INTEGER NOT NULL REFERENCES standort(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_messpunkt_standort ON messpunkt(standort_id);

            CREATE TABLE zuordnung (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
                messpunkt_id INTEGER NOT NULL REFERENCES messpunkt(id) ON DELETE CASCADE,
                gueltig_von TEXT NOT NULL,
                gueltig_bis TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_zuordnung_messpunkt ON zuordnung(messpunkt_id);
            CREATE INDEX idx_zuordnung_person ON zuordnung(person_id);

            CREATE TABLE readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                messpunkt_id INTEGER NOT NULL REFERENCES messpunkt(id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('bezug', 'einspeisung')),
                kwh REAL NOT NULL,
                source TEXT NOT NULL,
                import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL,
                UNIQUE (messpunkt_id, timestamp, direction)
            );
            CREATE INDEX idx_readings_messpunkt_ts ON readings(messpunkt_id, timestamp);

            CREATE TABLE billing_run_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                billing_run_id INTEGER NOT NULL REFERENCES billing_runs(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE RESTRICT,
                consumed_kwh REAL NOT NULL,
                produced_kwh REAL NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                net_amount_rappen INTEGER NOT NULL,
                pdf_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_items_run ON billing_run_items(billing_run_id);
        """,
    ),
    Migration(
        version=6,
        description="Add Person.anrede (salutation), Person.kundennummer "
        "(auto-assigned, unique, random 8-digit customer number) and "
        "Person.papierrechnung (paper-invoice opt-in), plus the fee "
        "settings/line items needed to bill it: "
        "LegSettings.verwaltungsaufwand_rp_per_kwh (admin surcharge on "
        "consumption), LegSettings.papierrechnung_rappen (flat paper-"
        "invoice fee), and their per-item counterparts on "
        "billing_run_items. Purely additive (existing rows keep their "
        "data; kundennummer starts NULL for existing Personen until "
        "re-saved -- there were none beyond demo data at this point).",
        sql="""
            ALTER TABLE person ADD COLUMN anrede TEXT NOT NULL DEFAULT '';
            ALTER TABLE person ADD COLUMN kundennummer INTEGER;
            CREATE UNIQUE INDEX idx_person_kundennummer ON person(kundennummer);
            ALTER TABLE person ADD COLUMN papierrechnung INTEGER NOT NULL DEFAULT 0;

            ALTER TABLE leg_settings ADD COLUMN verwaltungsaufwand_rp_per_kwh REAL NOT NULL DEFAULT 0;
            ALTER TABLE leg_settings ADD COLUMN papierrechnung_rappen INTEGER NOT NULL DEFAULT 0;

            ALTER TABLE billing_run_items ADD COLUMN verwaltungsaufwand_rappen INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE billing_run_items ADD COLUMN papierrechnung_rappen INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    Migration(
        version=7,
        description="Rename Trafokreis to LEG: what used to be a purely "
        "physical grid-topology grouping is now also the administrative "
        "billing entity -- by default a LEG matches one physical "
        "Trafokreis, but two Trafokreise can now deliberately share one "
        "custom-named LEG. Billing therefore moves from 'one run per "
        "quarter for the whole app' to 'one run per LEG per quarter' "
        "(billing_runs gains leg_id, its uniqueness constraint becomes "
        "(leg_id, period_year, period_quarter)). LegSettings.name is "
        "dropped -- the invoice letterhead now uses the relevant LEG's "
        "name instead; address, IBAN, price and the new admin fees stay "
        "global across all LEGs. No data migration/backfill: only demo/"
        "test data existed at this point, regenerated via the demo data "
        "generator after this runs.",
        sql="""
            ALTER TABLE trafokreis RENAME TO leg;

            ALTER TABLE standort RENAME COLUMN trafokreis_id TO leg_id;
            DROP INDEX IF EXISTS idx_standort_trafokreis;
            CREATE INDEX idx_standort_leg ON standort(leg_id);

            ALTER TABLE leg_settings DROP COLUMN name;

            DROP TABLE billing_run_items;
            DROP TABLE billing_runs;

            CREATE TABLE billing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                leg_id INTEGER NOT NULL REFERENCES leg(id) ON DELETE CASCADE,
                period_year INTEGER NOT NULL,
                period_quarter INTEGER NOT NULL CHECK (period_quarter BETWEEN 1 AND 4),
                created_at TEXT NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'erstellt',
                notes TEXT NOT NULL DEFAULT '',
                UNIQUE (leg_id, period_year, period_quarter)
            );

            CREATE TABLE billing_run_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                billing_run_id INTEGER NOT NULL REFERENCES billing_runs(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE RESTRICT,
                consumed_kwh REAL NOT NULL,
                produced_kwh REAL NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                verwaltungsaufwand_rappen INTEGER NOT NULL DEFAULT 0,
                papierrechnung_rappen INTEGER NOT NULL DEFAULT 0,
                net_amount_rappen INTEGER NOT NULL,
                pdf_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_items_run ON billing_run_items(billing_run_id);
        """,
    ),
    Migration(
        version=8,
        description="Split LEG and Trafokreis back apart: a Trafokreis is "
        "purely the physical grid-topology grouping (a property of the "
        "Standort, as before migration 7), while a LEG is now the "
        "administrative/billing group an individual Messpunkt opts into. "
        "Two Messpunkte at the same Standort (hence the same Trafokreis) "
        "can now belong to different LEGs, and one LEG can combine "
        "Messpunkte from several Trafokreise (at a correspondingly lower "
        "BKW discount, which this app never computes but can flag -- see "
        "app.domain.leg_composition). No data migration/backfill: only "
        "demo/test data existed at this point, regenerated via the demo "
        "data generator after this runs.",
        sql="""
            ALTER TABLE leg RENAME TO trafokreis;

            ALTER TABLE standort RENAME COLUMN leg_id TO trafokreis_id;
            DROP INDEX IF EXISTS idx_standort_leg;
            CREATE INDEX idx_standort_trafokreis ON standort(trafokreis_id);

            CREATE TABLE leg (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                bemerkung TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            ALTER TABLE messpunkt ADD COLUMN leg_id INTEGER REFERENCES leg(id) ON DELETE SET NULL;
            CREATE INDEX idx_messpunkt_leg ON messpunkt(leg_id);

            DROP TABLE billing_run_items;
            DROP TABLE billing_runs;

            CREATE TABLE billing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                leg_id INTEGER NOT NULL REFERENCES leg(id) ON DELETE CASCADE,
                period_year INTEGER NOT NULL,
                period_quarter INTEGER NOT NULL CHECK (period_quarter BETWEEN 1 AND 4),
                created_at TEXT NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'erstellt',
                notes TEXT NOT NULL DEFAULT '',
                UNIQUE (leg_id, period_year, period_quarter)
            );

            CREATE TABLE billing_run_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                billing_run_id INTEGER NOT NULL REFERENCES billing_runs(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE RESTRICT,
                consumed_kwh REAL NOT NULL,
                produced_kwh REAL NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                verwaltungsaufwand_rappen INTEGER NOT NULL DEFAULT 0,
                papierrechnung_rappen INTEGER NOT NULL DEFAULT 0,
                net_amount_rappen INTEGER NOT NULL,
                pdf_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_items_run ON billing_run_items(billing_run_id);
        """,
    ),
    Migration(
        version=9,
        description="Add Messpunkt.pv_leistung_kwp (installed PV capacity) "
        "and Messpunkt.batteriespeicher_kwh (battery storage capacity), "
        "both optional and purely informational -- neither feeds into "
        "distribution or billing. Purely additive.",
        sql="""
            ALTER TABLE messpunkt ADD COLUMN pv_leistung_kwp REAL;
            ALTER TABLE messpunkt ADD COLUMN batteriespeicher_kwh REAL;
        """,
    ),
    Migration(
        version=10,
        description="Drop Standort.netzebene: this deployment is always on "
        "NE7 (Niederspannung/Hausanschluss), so the field was dead weight "
        "-- every Standort has to pick the same value anyway.",
        sql="""
            ALTER TABLE standort DROP COLUMN netzebene;
        """,
    ),
    Migration(
        version=11,
        description="Rename Trafokreis.gemeinde to bkw_bezeichnung: 'Gemeinde' "
        "was being (mis)used to hold the official BKW Trafokreis "
        "designation/number, since 'Name' is meant for a self-chosen "
        "pseudo-name and the municipality itself never varies within one "
        "deployment (single-Gemeinde use). Municipality is therefore "
        "dropped as a concept here; the field is repurposed for its "
        "actual real-world use.",
        sql="""
            ALTER TABLE trafokreis RENAME COLUMN gemeinde TO bkw_bezeichnung;
        """,
    ),
    Migration(
        version=12,
        description="Split Person.name into firma/vorname/nachname: a "
        "Person can now be a company (firma), a natural person (vorname/"
        "nachname), or a company with a named contact person (all three). "
        "Purely additive/subtractive ALTER TABLE (add the three new "
        "columns, copy the old 'name' into 'vorname' verbatim -- no "
        "mechanical way to split it further -- then drop 'name'). "
        "person itself is never dropped or renamed, so zuordnung and "
        "billing_run_items (both REFERENCES person(id)) are completely "
        "untouched and keep their data. (An earlier version of this "
        "migration dropped and recreated person, zuordnung and "
        "billing_run_items on the incorrect assumption that only demo/test "
        "data existed at this point in the schema's history -- fixed here "
        "since it destroyed real data for anyone upgrading a live "
        "database; do not revert to that approach.)",
        sql="""
            ALTER TABLE person ADD COLUMN firma TEXT NOT NULL DEFAULT '';
            ALTER TABLE person ADD COLUMN vorname TEXT NOT NULL DEFAULT '';
            ALTER TABLE person ADD COLUMN nachname TEXT NOT NULL DEFAULT '';
            UPDATE person SET vorname = name;
            ALTER TABLE person DROP COLUMN name;
        """,
    ),
    Migration(
        version=13,
        description="Add Person.rechnungsadresse_hausnummer, split out of "
        "rechnungsadresse_strasse (same reasoning as Standort.hausnummer, "
        "migration 5): banks require the house number as its own field on "
        "payment forms. Purely additive; existing rows keep the house "
        "number embedded in rechnungsadresse_strasse until edited -- no "
        "reliable way to split free-text 'Strasse 12a' back out "
        "mechanically.",
        sql="""
            ALTER TABLE person ADD COLUMN rechnungsadresse_hausnummer TEXT NOT NULL DEFAULT '';
        """,
    ),
    Migration(
        version=14,
        description="Add Person.aktiv: instead of blocking deletion outright "
        "once billing history exists (ON DELETE RESTRICT on "
        "billing_run_items.person_id), the person is now deactivated "
        "instead -- kept for accounting/statistics but hidden from "
        "selection for new Zuordnungen. Purely additive; existing rows "
        "default to active.",
        sql="""
            ALTER TABLE person ADD COLUMN aktiv INTEGER NOT NULL DEFAULT 1;
        """,
    ),
    Migration(
        version=15,
        description="Add Person.bkw_kundennummer: the customer number BKW "
        "itself assigns, entered manually -- distinct from the app's own "
        "auto-generated Kundennummer. Nullable integer, optional (not "
        "known for every person yet). Purely additive.",
        sql="""
            ALTER TABLE person ADD COLUMN bkw_kundennummer INTEGER;
        """,
    ),
    Migration(
        version=16,
        description="Add LegSettings.extra_backup_dir: an optional second "
        "directory (e.g. a network drive) every backup is also copied "
        "into, in addition to the fixed backups/ folder. Empty string "
        "means no extra copy. Purely additive.",
        sql="""
            ALTER TABLE leg_settings ADD COLUMN extra_backup_dir TEXT NOT NULL DEFAULT '';
        """,
    ),
    Migration(
        version=17,
        description="Add LegSettings.messpunkt_land/messpunkt_identifikator: "
        "default values for a new Messpunkt's Land and (11-stellig) "
        "VSE-Identifikator -- always the same grid operator for a single "
        "LEG deployment, so entering them once in the settings saves "
        "re-typing them for every Messpunkt (still editable per "
        "Messpunkt). Purely additive; messpunkt_land defaults to 'CH'.",
        sql="""
            ALTER TABLE leg_settings ADD COLUMN messpunkt_land TEXT NOT NULL DEFAULT 'CH';
            ALTER TABLE leg_settings ADD COLUMN messpunkt_identifikator TEXT NOT NULL DEFAULT '';
        """,
    ),
    Migration(
        version=18,
        description="Add web_registration: an inbox for registrations "
        "submitted through the public form on leg-ittigen.ch (one row per "
        "Cloudflare submission, matched on email since a registration can "
        "report zero, one or several meters), plus web_registration_meter "
        "for its reported Zählernummern and LegSettings."
        "web_registration_cursor tracking the highest Cloudflare "
        "submission id already fetched. See app.importers."
        "registration_sync -- taking these into person/messpunkt/"
        "zuordnung stays a manual step via the existing CRUD pages, this "
        "is only the review inbox. (This migration's shape changed once "
        "before it ever shipped -- see git history -- safe to edit in "
        "place since no release ever depended on the earlier version.)",
        sql="""
            ALTER TABLE leg_settings ADD COLUMN web_registration_cursor INTEGER NOT NULL DEFAULT 0;

            CREATE TABLE web_registration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cloudflare_id INTEGER NOT NULL UNIQUE,
                firma TEXT NOT NULL DEFAULT '',
                anrede TEXT NOT NULL DEFAULT '',
                vorname TEXT NOT NULL DEFAULT '',
                nachname TEXT NOT NULL DEFAULT '',
                strasse TEXT NOT NULL DEFAULT '',
                hausnummer TEXT NOT NULL DEFAULT '',
                plz TEXT NOT NULL DEFAULT '',
                ort TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                telefon TEXT NOT NULL DEFAULT '',
                bkw_kundennummer TEXT NOT NULL DEFAULT '',
                iban TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                submitted_at TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                needs_review INTEGER NOT NULL DEFAULT 1,
                reviewed_at TEXT
            );
            CREATE INDEX idx_web_registration_needs_review ON web_registration(needs_review);
            CREATE INDEX idx_web_registration_email ON web_registration(email);

            CREATE TABLE web_registration_meter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                web_registration_id INTEGER NOT NULL REFERENCES web_registration(id) ON DELETE CASCADE,
                meter_number TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX idx_web_registration_meter_registration ON web_registration_meter(web_registration_id);
        """,
    ),
    Migration(
        version=19,
        description="Add web_registration.person_created: whether a Person "
        "was actually created from this registration via \"Person "
        "übernehmen\" (see app.gui.pages.web_registrierungen) -- distinct "
        "from needs_review/reviewed_at, which are also cleared by simply "
        "dismissing a registration without taking it over. Used to decide "
        "whether deleting the registration (which also deletes it from "
        "the remote leg-ittigen.ch Worker database, see app.importers."
        "cloudflare_client.delete_submissions) needs the strong "
        "irrevocable-data-loss warning: not needed once the data already "
        "lives on in a Person record. Purely additive.",
        sql="""
            ALTER TABLE web_registration ADD COLUMN person_created INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    Migration(
        version=20,
        description="Add person_onboarding: tracks a person's progress "
        "through the five real-world steps between an interested party's "
        "registration and full LEG membership (Anmeldung bei uns -> "
        "Einteilung in LEG -> Gesellschaftsvertrag -> Anmeldung bei der "
        "BKW -> Bestätigung durch die BKW), see app.models.person_onboarding. "
        "Deliberately a separate, optional table rather than columns on "
        "person: a tracking row only exists once explicitly started (auto- "
        "started by \"Person übernehmen\", or manually), so existing "
        "persons never retroactively appear as having an overdue step. "
        "Also adds LegSettings.onboarding_ueberfaellig_tage, the "
        "configurable threshold (default 30 days) for flagging a step as "
        "open too long. Purely additive.",
        sql="""
            ALTER TABLE leg_settings ADD COLUMN onboarding_ueberfaellig_tage INTEGER NOT NULL DEFAULT 30;

            CREATE TABLE person_onboarding (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL UNIQUE REFERENCES person(id) ON DELETE CASCADE,
                angemeldet_am TEXT,
                leg_zugewiesen_am TEXT,
                leg_id INTEGER REFERENCES leg(id) ON DELETE SET NULL,
                vertrag_unterzeichnet_am TEXT,
                bkw_angemeldet_am TEXT,
                bkw_bestaetigt_am TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_person_onboarding_person ON person_onboarding(person_id);
        """,
    ),
    Migration(
        version=21,
        description="Shorten Person.kundennummer from 8 to 6 digits (see "
        "app.models.person.generate_kundennummer), shown in two "
        "3-digit blocks. Reassigns a fresh, unique 6-digit Kundennummer "
        "to every already-existing Person -- only ever affects future "
        "invoices/QR references, never ones already sent out, since "
        "generate_qrr_reference reads the Kundennummer fresh at PDF "
        "generation time rather than storing it on past billing rows.",
        sql="""
            UPDATE person
            SET kundennummer = 100000 + (ABS(RANDOM()) % 900000)
            WHERE kundennummer IS NOT NULL;
        """,
    ),
    Migration(
        version=22,
        description="Extend Web-Registrierungen take-over tracking to "
        "Standort and Messpunkt (previously only Person, see migration "
        "19's person_created): add web_registration.standort_created and "
        "web_registration_meter.messpunkt_created, both set only by "
        "their own \"... übernehmen\" action. Drop web_registration."
        "needs_review/reviewed_at: the explicit \"als geprüft "
        "markieren\" review step is replaced by simply checking whether "
        "Person/Standort/every reported Messpunkt have been taken over "
        "(see WebRegistration.is_fully_processed), or deleting the entry "
        "once nothing more needs doing.",
        sql="""
            ALTER TABLE web_registration ADD COLUMN standort_created INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE web_registration_meter ADD COLUMN messpunkt_created INTEGER NOT NULL DEFAULT 0;
            DROP INDEX idx_web_registration_needs_review;
            ALTER TABLE web_registration DROP COLUMN needs_review;
            ALTER TABLE web_registration DROP COLUMN reviewed_at;
        """,
    ),
    Migration(
        version=23,
        description="Add invoice email support: billing_run_items."
        "email_sent_at (set once an invoice has actually been emailed, "
        "see app.emailing.bulk_send.send_invoice_emails -- prevents an "
        "accidental duplicate bulk send, while resend_invoice_email can "
        "still force a specific resend) and leg_settings."
        "rechnung_email_betreff/rechnung_email_text (the reusable "
        "invoice email template, written once in Einstellungen instead "
        "of retyped every quarter). Purely additive.",
        sql="""
            ALTER TABLE billing_run_items ADD COLUMN email_sent_at TEXT;

            ALTER TABLE leg_settings ADD COLUMN rechnung_email_betreff TEXT NOT NULL
                DEFAULT 'Ihre Abrechnung {leg}, Q{quartal} {jahr}';
            ALTER TABLE leg_settings ADD COLUMN rechnung_email_text TEXT NOT NULL
                DEFAULT 'Guten Tag {anrede} {nachname}

Im Anhang finden Sie Ihre Abrechnung für {leg}, Q{quartal} {jahr} über CHF {betrag}.

Freundliche Grüsse';
        """,
    ),
    Migration(
        version=24,
        description="Add email_broadcast_log: a sent-history record for "
        "broadcast/LEG emails (see app.emailing.bulk_send."
        "send_broadcast_email), analogous to billing_run_items."
        "email_sent_at for invoices. Records only the recipients a send "
        "actually succeeded for, so an interrupted batch stays "
        "traceable. Purely additive.",
        sql="""
            CREATE TABLE email_broadcast_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at TEXT NOT NULL,
                scope TEXT NOT NULL,
                leg_id INTEGER REFERENCES leg(id) ON DELETE SET NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                recipient_count INTEGER NOT NULL,
                recipient_emails TEXT NOT NULL
            );
        """,
    ),
    Migration(
        version=25,
        description="Add Debitoren ledger and camt.053/camt.054 bank "
        "reconciliation: account_entries records money the bank actually "
        "confirmed (payments received, payouts executed, manual "
        "corrections) against a Person's running account -- never a copy "
        "of billing_run_items.net_amount_rappen, which stays the sole "
        "source of what was invoiced (see app.models.account_entry for "
        "the sign-convention glossary). bank_import_batches/"
        "bank_transactions track every imported bank statement entry, "
        "matched or not, so nothing imported is ever silently lost. "
        "Purely additive.",
        sql="""
            CREATE TABLE account_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE RESTRICT,
                kind TEXT NOT NULL CHECK (kind IN ('zahlungseingang', 'auszahlung', 'korrektur')),
                amount_rappen INTEGER NOT NULL,
                booked_at TEXT NOT NULL,
                billing_run_item_id INTEGER REFERENCES billing_run_items(id) ON DELETE SET NULL,
                bank_transaction_id INTEGER REFERENCES bank_transactions(id) ON DELETE SET NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_account_entries_person ON account_entries(person_id);

            CREATE TABLE bank_import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                account_iban TEXT NOT NULL DEFAULT '',
                statement_from TEXT,
                statement_to TEXT,
                entry_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE bank_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bank_import_batch_id INTEGER NOT NULL REFERENCES bank_import_batches(id) ON DELETE CASCADE,
                bank_reference TEXT NOT NULL,
                booking_date TEXT NOT NULL,
                amount_rappen INTEGER NOT NULL,
                currency TEXT NOT NULL,
                credit_debit_indicator TEXT NOT NULL CHECK (credit_debit_indicator IN ('CRDT', 'DBIT')),
                counterparty_name TEXT NOT NULL DEFAULT '',
                counterparty_iban TEXT NOT NULL DEFAULT '',
                structured_reference TEXT NOT NULL DEFAULT '',
                remittance_text TEXT NOT NULL DEFAULT '',
                source_format TEXT NOT NULL CHECK (source_format IN ('camt053', 'camt054')),
                is_reversal INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'unmatched' CHECK (
                    status IN ('auto_matched', 'suggested_pending_review', 'manually_matched', 'unmatched', 'ignored')
                ),
                matched_person_id INTEGER REFERENCES person(id) ON DELETE SET NULL,
                account_entry_id INTEGER REFERENCES account_entries(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                UNIQUE (bank_reference, currency, amount_rappen, credit_debit_indicator)
            );
            CREATE INDEX idx_bank_transactions_status ON bank_transactions(status);
        """,
    ),
    Migration(
        version=26,
        description="Add Mahnwesen: billing_run_items.faellig_am (the due "
        "date actually printed on the invoice, persisted so overdue-ness "
        "can be checked later -- previously only computed on the fly at "
        "PDF-generation time and never stored), mahnstufe/"
        "letzte_mahnung_am (per-item escalation state, 0/1/2 per the "
        "LEG's own 2-stage Reglement: 1. Mahnung grants a new deadline, "
        "2. Mahnung triggers an exclusion review -- never automatic, see "
        "app.domain.mahnwesen), leg_settings' editable Mahnung templates "
        "and mahnung_log (a sent-history record, analogous to "
        "email_broadcast_log). Purely additive.",
        sql="""
            ALTER TABLE billing_run_items ADD COLUMN faellig_am TEXT;
            ALTER TABLE billing_run_items ADD COLUMN mahnstufe INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE billing_run_items ADD COLUMN letzte_mahnung_am TEXT;

            ALTER TABLE leg_settings ADD COLUMN mahnung_neue_frist_tage INTEGER NOT NULL DEFAULT 14;
            ALTER TABLE leg_settings ADD COLUMN mahnung_bagatellgrenze_rappen INTEGER NOT NULL DEFAULT 500;
            ALTER TABLE leg_settings ADD COLUMN mahnung1_email_betreff TEXT NOT NULL
                DEFAULT 'Zahlungserinnerung -- Ihre Rechnung ist noch offen';
            ALTER TABLE leg_settings ADD COLUMN mahnung1_email_text TEXT NOT NULL
                DEFAULT 'Guten Tag {anrede} {nachname}

Wir konnten bisher keinen Zahlungseingang für Ihre Rechnung(en) über CHF {betrag} feststellen. Wir bitten Sie, den ausstehenden Betrag bis zum {neue_frist} zu begleichen.

Sollte diese Frist ungenutzt verstreichen, behalten wir uns vor, Ihre Mitgliedschaft in der LEG zu kündigen. Die Forderung bleibt davon unberührt bestehen.

Den Einzahlungsschein finden Sie im Anhang.

Freundliche Grüsse';
            ALTER TABLE leg_settings ADD COLUMN mahnung2_email_betreff TEXT NOT NULL
                DEFAULT '2. Mahnung -- Kündigung der Mitgliedschaft';
            ALTER TABLE leg_settings ADD COLUMN mahnung2_email_text TEXT NOT NULL
                DEFAULT 'Guten Tag {anrede} {nachname}

Trotz unserer Zahlungserinnerung ist Ihre Rechnung über CHF {betrag} weiterhin nicht beglichen. Wir sehen uns daher gezwungen, Ihre Mitgliedschaft in der LEG zu kündigen.

Die offene Forderung bleibt davon unberührt bestehen.

Den Einzahlungsschein finden Sie im Anhang.

Freundliche Grüsse';

            CREATE TABLE mahnung_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at TEXT NOT NULL,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
                stufe INTEGER NOT NULL,
                betrag_rappen INTEGER NOT NULL,
                billing_run_item_ids TEXT NOT NULL
            );
        """,
    ),
    Migration(
        version=27,
        description="Add Austritts-/Ausschlussprozess: person_offboarding "
        "tracks the real-world steps of a LEG membership ending, exactly "
        "mirroring app.models.person_onboarding's structure (a fixed, "
        "unordered set of step dates) but for the reverse direction --  "
        "usable both for a voluntary exit and for the exclusion review "
        "the Mahnwesen's 2. Mahnung triggers (see app.domain.mahnwesen), "
        "distinguished by 'grund'. Ending a membership never affects the "
        "Debitoren claim itself -- that lives entirely in "
        "billing_run_items/account_entries, untouched by this table. "
        "Purely additive.",
        sql="""
            CREATE TABLE person_offboarding (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
                grund TEXT NOT NULL CHECK (grund IN ('zahlungsverzug', 'freiwillig', 'sonstig')),
                beschlossen_am TEXT,
                messpunkt_austritt_am TEXT,
                bkw_informiert_am TEXT,
                person_bestaetigt_am TEXT,
                created_at TEXT NOT NULL
            );
        """,
    ),
    Migration(
        version=28,
        description="Split the single administrative surcharge "
        "(LegSettings.verwaltungsaufwand_rp_per_kwh, charged on "
        "consumption only) into two independent rates -- Bezug and "
        "Einspeisung -- settable separately, each defaulting to the "
        "previous single default of 0.5 Rp./kWh. billing_run_items."
        "verwaltungsaufwand_rappen is renamed to "
        "verwaltungsaufwand_bezug_rappen (a pure rename, existing amounts "
        "untouched) and gains a verwaltungsaufwand_einspeisung_rappen "
        "counterpart, plus the two actual per-kWh rates used are now "
        "frozen directly on the item (verwaltungsaufwand_bezug_rp_per_kwh/"
        "_einspeisung_rp_per_kwh) -- mirroring how billing_run_items."
        "price_rp_per_kwh already freezes the energy price at billing "
        "time. Previously, the rate shown alongside an already-billed fee "
        "was re-read live from leg_settings, so changing the rate in "
        "Einstellungen would silently change what an old invoice appears "
        "to have charged, even though the actual billed Rappen amount "
        "never changed -- this migration fixes that display bug going "
        "forward by giving every item its own frozen rate. Existing rows "
        "are backfilled with the (just-migrated) current Bezug rate only "
        "where they actually carried a nonzero Bezug fee -- the best "
        "available approximation for pre-existing data, since the exact "
        "historical rate was never recorded before this migration.",
        sql="""
            ALTER TABLE leg_settings ADD COLUMN verwaltungsaufwand_bezug_rp_per_kwh REAL NOT NULL DEFAULT 0.5;
            ALTER TABLE leg_settings ADD COLUMN verwaltungsaufwand_einspeisung_rp_per_kwh REAL NOT NULL DEFAULT 0.5;
            UPDATE leg_settings SET verwaltungsaufwand_bezug_rp_per_kwh = verwaltungsaufwand_rp_per_kwh;
            ALTER TABLE leg_settings DROP COLUMN verwaltungsaufwand_rp_per_kwh;

            ALTER TABLE billing_run_items RENAME COLUMN verwaltungsaufwand_rappen TO verwaltungsaufwand_bezug_rappen;
            ALTER TABLE billing_run_items ADD COLUMN verwaltungsaufwand_einspeisung_rappen INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE billing_run_items ADD COLUMN verwaltungsaufwand_bezug_rp_per_kwh REAL NOT NULL DEFAULT 0;
            ALTER TABLE billing_run_items ADD COLUMN verwaltungsaufwand_einspeisung_rp_per_kwh REAL NOT NULL DEFAULT 0;
            UPDATE billing_run_items SET verwaltungsaufwand_bezug_rp_per_kwh =
                (SELECT verwaltungsaufwand_bezug_rp_per_kwh FROM leg_settings WHERE id = 1)
                WHERE verwaltungsaufwand_bezug_rappen > 0;
        """,
    ),
    Migration(
        version=29,
        description="Add email signatures (app.models.signature): named, "
        "reusable text blocks maintained on their own page under "
        "Kommunikation (/signaturen), selectable per send on the "
        "E-Mail-Versand page (app.gui.pages.email_versand) -- 'keine "
        "Signatur' remains the default, nothing is appended unless "
        "explicitly chosen. Purely additive.",
        sql="""
            CREATE TABLE signatures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """,
    ),
    Migration(
        version=30,
        description="Fix two Mahnwesen correctness gaps found in review: "
        "(1) billing_run_items.faellig_am was only ever set by "
        "export_billing_run_documents, so every item billed before "
        "migration 26 has faellig_am = NULL and was silently invisible to "
        "app.domain.mahnwesen forever -- backfilled here as "
        "created_at + 45 days (PAYMENT_TERM) for every already-issued item "
        "(pdf_path set), the same best-effort 'closest available "
        "approximation' precedent as migration 28's rate backfill, since "
        "the exact original due date was never recorded before migration "
        "26. (2) billing_run_items.mahnung_frist_tage freezes the new-"
        "deadline period (LegSettings.mahnung_neue_frist_tage) actually "
        "granted when an item's 1. Mahnung was sent, so a later change to "
        "that setting can never retroactively move the deadline already "
        "promised in writing to a specific person -- mirrors migration "
        "28's per-item rate freeze for the same reason. Purely additive.",
        sql="""
            UPDATE billing_run_items
            SET faellig_am = date(created_at, '+45 days')
            WHERE faellig_am IS NULL AND pdf_path IS NOT NULL;

            ALTER TABLE billing_run_items ADD COLUMN mahnung_frist_tage INTEGER;
        """,
    ),
    Migration(
        version=31,
        description="Add Anschlussleistung tracking (Standort) and a "
        "Betriebsstunden<=500h flag (Messpunkt) for the BKW 5%-Produktions-"
        "regel check (Art. 19e Abs. 1 StromVV, see app.domain.anschluss_ratio): "
        "a LEG's production capacity must be at least 5% of its participants' "
        "connection capacity, else it must be reported to BKW (Art. 19g Abs. 1 "
        "lit. e StromVV). Anschlussleistung is not available via any BKW API "
        "(gridconnection.bkw.ch only shows one's own connection, not other "
        "participants'), so it is recorded manually per Standort, with a "
        "Gebaeudetyp-based Richtwert fallback when no real value is known. "
        "Purely additive.",
        sql="""
            ALTER TABLE standort ADD COLUMN anschlussleistung_kw REAL;
            ALTER TABLE standort ADD COLUMN anschlussleistung_quelle TEXT
                CHECK (anschlussleistung_quelle IN ('bkw_abfrage', 'anschlussvertrag', 'richtwert'));
            ALTER TABLE standort ADD COLUMN anschlussleistung_erfasst_am TEXT;
            ALTER TABLE standort ADD COLUMN gebaeudetyp TEXT NOT NULL DEFAULT 'unbekannt'
                CHECK (gebaeudetyp IN ('efh', 'mfh_1_3', 'mfh_4_9', 'mfh_10_15', 'gewerbe', 'unbekannt'));

            ALTER TABLE messpunkt ADD COLUMN betrieb_max_500h INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    Migration(
        version=32,
        description="Remove migration 31's Anschlussleistung/5%-Produktionsregel "
        "tracking again: the fixed-Richtwert-per-Gebaeudetyp model turned out not "
        "to fit reality well enough to be worth the manual data entry it required "
        "(see app.domain.participant_mix, which replaces it with a much simpler "
        "Prosumer/Consumer participant-count ratio -- no Anschlussleistung, "
        "Gebaeudetyp or 500h-Betriebsstunden concept needed at all). Reverts "
        "standort.anschlussleistung_kw/_quelle/_erfasst_am/gebaeudetyp and "
        "messpunkt.betrieb_max_500h.",
        sql="""
            ALTER TABLE standort DROP COLUMN anschlussleistung_kw;
            ALTER TABLE standort DROP COLUMN anschlussleistung_quelle;
            ALTER TABLE standort DROP COLUMN anschlussleistung_erfasst_am;
            ALTER TABLE standort DROP COLUMN gebaeudetyp;

            ALTER TABLE messpunkt DROP COLUMN betrieb_max_500h;
        """,
    ),
    Migration(
        version=33,
        description="Add leg_settings.zuordnung_termine_ignorieren: a global, "
        "off-by-default switch for whether the Trafokreis/LEG Prosumer:Consumer "
        "overview (app.domain.participant_mix) and the 'Personen einer LEG' "
        "E-Mail-Empfaengerliste (app.emailing.bulk_send.list_leg_recipients) "
        "count a Zuordnung whose gueltig_von has not started yet -- found via a "
        "real customer database where every Zuordnung was pre-entered for the "
        "following quarter, making every overview show 0:0 and every LEG email "
        "have zero recipients until that date actually arrived. Off (0, "
        "'Termine beruecksichtigen') matches the strict, pre-existing "
        "Zuordnung.covers() behaviour; on (1) switches every affected view to "
        "Zuordnung.is_current_or_upcoming() instead. Purely additive.",
        sql="""
            ALTER TABLE leg_settings ADD COLUMN zuordnung_termine_ignorieren INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    Migration(
        version=34,
        description="Remove migration 33's global switch again: rather than a "
        "toggle, ignoring a Zuordnung's start date is now simply the permanent "
        "behaviour everywhere it matters (Trafokreis/LEG Prosumer:Consumer "
        "overview, LEG-E-Mail-Empfaengerliste, and now also the 'currently "
        "assigned person' shown on Messpunkte/Standorte/Personen -- see "
        "app.models.zuordnung.get_relevant_for_messpunkt) -- billing/"
        "distribution and the historical reading-completeness check keep using "
        "the strict Zuordnung.covers() unaffected by any of this, as they "
        "always have. A not-yet-started Zuordnung is marked in the UI instead "
        "of hidden, so what is upcoming vs. already active stays visible "
        "without a setting to remember. Drops leg_settings."
        "zuordnung_termine_ignorieren.",
        sql="""
            ALTER TABLE leg_settings DROP COLUMN zuordnung_termine_ignorieren;
        """,
    ),
    Migration(
        version=35,
        description="Add leg_settings.leg_gruendung_min_personen: the "
        "minimum number of people (app.domain.participant_mix.ParticipantMix."
        "gesamt_personen -- Prosumer- plus Consumer-count) a Trafokreis must "
        "have, in addition to already having both a Prosumer and a Consumer, "
        "before the app suggests splitting it off its current multi-"
        "Trafokreis LEG into its own, better-discounted one (Trafokreise/LEGs "
        "overview badges and the dashboard's price-optimization hint). "
        "Editable in Einstellungen; defaults to 7. Purely additive.",
        sql="""
            ALTER TABLE leg_settings ADD COLUMN leg_gruendung_min_personen INTEGER NOT NULL DEFAULT 7;
        """,
    ),
    Migration(
        version=36,
        description="Add email_broadcast_log.attachment_filename: records "
        "the name of the file (if any) an administrator attached to a "
        "broadcast/LEG email send (app.emailing.bulk_send.send_broadcast_email, "
        "app.gui.pages.email_versand), so the sent-history stays a complete "
        "record of what was actually sent. NULL for a send without an "
        "attachment, including every existing row. Purely additive.",
        sql="""
            ALTER TABLE email_broadcast_log ADD COLUMN attachment_filename TEXT;
        """,
    ),
]
