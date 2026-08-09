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
]
