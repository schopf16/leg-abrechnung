# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A desktop app (German UI) for the administrator of a Swiss "lokale
Elektrizitätsgemeinschaft" (LEG, local electricity community) in BKW's grid
territory: import meter readings, distribute locally-shared solar
production at 15-minute resolution, and generate quarterly invoices/credit
notes as PDFs with a Swiss QR-bill. Runs fully offline as a native desktop
window (NiceGUI + pywebview) backed by a local SQLite database — no server,
no cloud, no network dependency for normal operation. See `README.md`
(German) for the full user-facing feature description.

## Commands

```
# Run the app (native desktop window)
.venv\Scripts\python.exe run.py          # or double-click start.bat

# Run the full test suite
.venv\Scripts\python.exe -m pytest

# Run a single test file / test
.venv\Scripts\python.exe -m pytest tests/test_billing.py
.venv\Scripts\python.exe -m pytest tests/test_billing.py::test_name -v
```

Lint, security scan and tests are the verification gate — see "Tooling and
CI" below (ruff, bandit, pip-audit); there is no typecheck step. `.venv` is a local, gitignored virtualenv set up by
`start.bat`; `requirements.txt` pins exact versions.

## Architecture

### Layering (strict, don't blur it)

- `app/models/` — one module per DB table, pure CRUD, no business logic.
  Each row-representing dataclass has a `from_row(sqlite3.Row)` static
  constructor.
- `app/domain/` — business logic. Knows nothing about SQL statements
  (calls into `app/models/`) or NiceGUI.
- `app/gui/pages/` — one module per page/route (`@ui.page("/...")`),
  registered by importing `app/gui/pages/__init__.py` (side-effecting
  import in `app/main.py`). Calls into `app/domain/` and `app/models/`,
  never embeds SQL or business rules itself.
- `app/importers/` — file-format parsers (EBIX/CSV meter readings, camt.053/
  camt.054 bank statements), isolated from the rest of the app behind a
  parsed-record dataclass (e.g. `ParsedReading`, `ParsedBankTransaction`) so
  a format-specific quirk fix never leaks elsewhere.
- `app/pdf/` — PDF/QR-bill generation (`qrbill` + `reportlab` + `svglib`)
  and CSV export lists.
- `app/emailing/` — Microsoft Graph API client (not SMTP — Basic Auth is
  disabled for Exchange Online), template placeholder substitution, and
  send orchestration for broadcasts/invoices.
- `app/db/` — `migrations.py` (schema history) + `schema.py`
  (`initialize_database`, applies pending migrations in order) +
  `connection.py` (`connection_scope()` context manager).
- `app/backup/` — DB backup/restore via SQLite's online backup API.

### Database access pattern

Every write path goes through `app.db.connection.connection_scope()`:

```python
with connection_scope() as connection:
    ...
```

It commits on clean exit, rolls back and re-raises on exception. Individual
repo functions in `app/models/` also call `connection.commit()` themselves
by default (so they work standalone in tests using the raw `db` fixture
connection too) — several accept a `commit: bool = True` kwarg so a bulk
caller looping over many rows in one request (e.g. importing a bank
statement) can pass `commit=False` and let the enclosing `connection_scope`
commit once at the end instead of once per row.

### Migrations

`app/db/migrations.py`'s `MIGRATIONS` list is the only way the schema
changes: append a new `Migration(version=last+1, description=..., sql=...)`.
**Never renumber or edit an existing entry** — old backups get migrated
forward step by step when opened, so history must stay replayable. Before
applying a new migration to the real `data/leg_abrechnung.sqlite3`, test it
against a scratch copy first, and take a safety backup
(`app.backup.backup_service.create_backup()`) before applying it for real.

### Money handling

All monetary amounts are integer **Rappen** (`*_rappen` fields), never
`float`/`Decimal` in the DB or in domain logic — `Decimal` only appears at
the PDF-rendering boundary when formatting for the QR-bill library. Energy
amounts (kWh) stay full-precision floats through intermediate calculations
and are rounded to the Rappen exactly once, at the final net settlement
(see `app/domain/billing.py`'s module docstring) — never re-rounded and
re-added, so rounding error can't compound across a document.

`app/models/account_entry.py`'s module docstring defines the sign
convention for the Debitoren ledger: internally, positive = person owes
the LEG, negative = LEG owes the person; an incoming payment is stored
negative (it reduces debt). The GUI negates this **exactly once**, at the
point the Saldo is displayed — never earlier, never in a model or domain
function.

### "Frozen at the time of the action" — a recurring pattern

Several rates/values are copied onto a row at the moment something happens
(a billing run is created, a Mahnung is sent) specifically so a later
change to `LegSettings` can never retroactively alter what was already
communicated to a member. Existing examples on `BillingRunItem`:
`price_rp_per_kwh`, `admin_fee_consumption_rp_per_kwh`/
`admin_fee_feed_in_rp_per_kwh`, `due_date` (printed verbatim on re-export,
never recomputed), `dunning_deadline_days` (the deadline actually granted
by a 1. Mahnung). When adding a new
settings-driven value that gets billed/communicated to a person, default to
this pattern rather than reading the live setting at render time.

### Sorting: one mechanism, every list

Every *browsable* list — the CRUD pages and worklists under
`app/gui/pages/` — sorts through `app/gui/sorting.py` and nothing else: a
module-level `SORT_OPTIONS` list of `SortOption`s (default first), a
`render_sort_select(SORT_OPTIONS, ...)` as the **last control in the
page's filter row**, and `apply_sort(rows, SORT_OPTIONS, sort_select)`
where the page builds its visible rows. `render_sort_select` returns a
`SortControl` (select + ascending/descending arrow), not a bare
`ui.select`; pass the whole control to `apply_sort`/`sort_description` so
the direction is honoured, never just its `.value`. Never Quasar's `"sortable": True`
column headers — half the lists are cards and have no header to click, so
clickable headers could never be the mechanism that works everywhere, and
two mechanisms is exactly what the user complained about.

Sort in Python on already-loaded rows, not in the repo's `ORDER BY`:
SQLite's BINARY/NOCASE collation mis-sorts umlauts — a non-leading one
slips past its own initial group ("Bühler" after "Burri"), a leading one
goes behind every "Z…" name ("Ärni" last of all). Build keys from `text_key`,
`number_key`, `address_key` (house numbers numerically) and
`person_name_key` (surname, falling back to the company name) rather than
hand-rolling per page — a person or an address must come out in the same
order on every page that lists it. Prefer negating a number in the key
over `SortOption.reverse`, which also flips the text tiebreak. Person
lists default to `"last_name"`/"Nachname"; a worklist may lead with its
own urgency order (see `app/gui/pages/dunning.py`), and an inbox with
newest-first (`web_registrations.py`). A list with only one sensible order
just sorts that way and shows no control (`signatures.py`). Whatever is
selected is printed on the printout's own "Sortierung:" line, via
`render_print_button`'s `get_sort_description` — never folded into the
filter line, because a sort order is not a filter.

Detail sub-tables and history tables are exempt and have no control:
`billing.py` (runs and items), `backup.py`, `import_page.py`,
`reports.py`, `dashboard.py`, and the metering-point tables on the
Person and Standort detail pages. Each shows one context's rows in the
one order that context implies, so there is no choice to offer.

### Onboarding/offboarding-style trackers

`app/models/person_onboarding.py` and `app/models/person_offboarding.py`
follow an identical shape: a `STEPS` list of (attribute, label) tuples,
each an optional date field filled in as a real-world step completes,
`start_for_person()` idempotent creation, `current_step`/`is_complete`/
`is_overdue` helpers. Their GUI dialogs (`app/gui/onboarding_form.py`,
`app/gui/offboarding_form.py`) are likewise near-identical. Follow this
shape for any future multi-step, manually-confirmed real-world process
instead of inventing a new tracker shape.

### Language: English code, German UI

Identifiers, file names, the database schema, docstrings and comments are
English. Only user-facing text is German (labels, buttons, notifications,
user-facing error messages, PDF/CSV output, and the `{vorname}`-style
placeholders in the administrator's stored email templates). Two things
deliberately keep German *string values*: the external leg-ittigen.ch
form payload keys in `app/importers/cloudflare_client.py` (an API contract
we don't control) and the template placeholder keys in
`app/emailing/templates.py`/`app/domain/dunning.py`. Persisted enum
values are English since migration 43 (`direction` 'consumption'/
'feed_in', `person_offboarding.reason` 'payment_default'/'voluntary'/
'other', `account_entries.kind` 'payment_received'/'payout'/'correction',
`billing_runs.status` 'created', `email_broadcast_log.scope` 'all'/'leg',
`leg.discount_level` 'high'/'low'/'unknown');
the importers still accept the German spellings from BKW files
(`app.importers.base.validate_direction`). Old migrations keep their
original German SQL forever (replay history, see above).

Glossary (German domain term → code name):

| German | Code |
|---|---|
| Trafokreis | `SubstationArea`, `substation_area` |
| Standort | `Site`, `site` |
| Messpunkt / Messpunktbezeichnung | `MeteringPoint` / `designation` |
| Messrichtung Bezug / Einspeisung | `direction` `DIRECTION_CONSUMPTION` / `DIRECTION_FEED_IN` |
| Zuordnung (gültig von/bis) | `Assignment` (`valid_from`/`valid_to`) |
| Kundennummer | `customer_number` |
| Verwaltungsaufwand | `admin_fee_*` |
| Mahnwesen / Mahnung / Mahnstufe | `dunning` / dunning notice / `dunning_level` |
| Fällig am | `due_date` |
| Saldo | `balance` |
| Stichtag | `reference_date` |
| Aufnahme / Austritt | onboarding / offboarding |
| Rabattstufe (BKW, 40%/20% auf die Netznutzung) | `discount_level` `high`/`low` |
| Produzent / Konsument (Messrichtung, nicht Person) | `producer_count` / `consumer_count` |
| LEG, BKW, Rappen, QR-Rechnung | unchanged (proper nouns) |

### Domain model core

A LEG also carries `discount_level`: the BKW discount tier on the
Netznutzung (40% without a transformation stage, 20% with one — BKW calls
these "hohe"/"niedrige Rabattstufe", **not** "Anschlussleistung", which is
a kW connection rating). It is entered by hand and never derived: it
follows from BKW's grid topology and is confirmed by BKW per location.
Whether a LEG spans several substation areas (`app.domain.leg_composition`)
correlates with it but is a different statement, so the app must not
overwrite one with the other.

In `app.domain.participant_mix`, **Producer** means the feed-in side and
**Consumer** the consumption side — the split is per MeteringPoint
direction, not per person. Someone with both is counted on both sides;
that person is the only real "Prosumer", a word this module deliberately
no longer uses for the producer side, and the one place the word must
stay. The German UI says "Produzent"/"Konsument" (BKW's own words on
their LEG pages); only the identifiers are English.

`SubstationArea` (BKW Trafokreis, physical) → `Site` (physical connection
site with an address) → `MeteringPoint` (a meter, consumption or feed-in
direction) → `Assignment` (time-bounded link from a metering point to a
`Person`, so a mid-quarter move splits readings automatically). `Leg` is
the billing group a metering point is assigned to — normally one per
substation area, but can span several if their owners pool together (the
app warns about this on several pages but doesn't compute the resulting
BKW discount itself). `BillingRun`/`BillingRunItem` are produced per LEG
per quarter from `app.domain.distribution.compute_quarter_distribution`.

### Bank reconciliation / dunning / offboarding (receivables feature set)

`app/pdf/qr_reference.py`'s `generate_qrr_reference`/`parse_qrr_reference`
are pure functions encoding `(customer_number, billing_run_id, item_id)`
into a QRR reference number and back — nothing about a sent invoice's
reference is stored in the DB; a bank statement match is decoded straight
back to the exact `BillingRunItem`. `app/domain/bank_reconciliation.py`
matches imported camt.053/camt.054 transactions to a person (auto via
decoded QRR reference, or a ranked suggestion via IBAN/customer-number-in-
text/name similarity) and books an `AccountEntry`. `app/domain/dunning.py`
implements the LEG's own 2-stage Mahnung Reglement (not the generic
3-stage/fee model) gated on the person's overall balance, not just one
item's own state — see its module docstring before changing escalation
logic.

### Tooling and CI

`requirements-dev.txt` adds ruff, bandit and pip-audit on top of
`requirements.txt`; `pyproject.toml` holds their configuration.
`.github/workflows/ci.yml` runs on every push/PR: `ruff check`, the pytest
suite, `bandit -ll` (medium severity and up) and `pip-audit`. Keep all four
green locally before pushing:

```
.venv\Scripts\ruff.exe check app tests run.py
.venv\Scripts\ruff.exe format --check app tests run.py
.venv\Scripts\bandit.exe -q -r app -ll
.venv\Scripts\pip-audit.exe -r requirements.txt
```

Formatting is `ruff format` (line length 110, see `pyproject.toml`) and is
enforced in CI; run `ruff format app tests run.py` before committing.

### Security review is Claude's job, not GitHub's

On GitHub this repository runs **CodeQL** (default setup), **secret
scanning** with push protection, and **Dependabot** alerts plus security
updates. Two GitHub features are deliberately **off** and must stay off:
**AI Scan** and **Copilot Autofix** — both require a paid Copilot licence
and AI credits this account does not have, and left on they fail red with
`You are not licensed to use Copilot` on every single PR. If that red
check reappears, it is a licence error, not a finding: check the job log
before treating it as one.

Note what is and is not lost by that. AI Scan only covers languages
CodeQL does not, and this codebase is Python plus GitHub Actions, both of
which CodeQL handles — so nothing stops being *detected*. Copilot Autofix
only *suggests patches* for CodeQL alerts. The gap is therefore the
suggested fix and a second pair of eyes, not the detection.

**Therefore, on every review and before every push:** review the change
for security implications yourself — do not rely on GitHub to raise them.
Use the `security-review` skill on the branch diff. Pay attention to what
bandit's pattern matching and CodeQL's dataflow do not cover well and
what this app actually handles: personal data of real members (names,
addresses, IBANs, emails), the Microsoft Graph credentials and the
leg-ittigen.ch API token (`app/config.py`), SQL built by string
formatting, anything written to `logs/` (log lines can contain real names
and amounts — see `app/logging_setup.py`), file paths taken from user
input, and the QR-bill/Rappen arithmetic, where a wrong number is a wrong
invoice to a real person. When CodeQL does flag something, propose the
fix in the PR — that is exactly the part Autofix would have done.

This is a convention, not an enforced hook: it works because this file is
read at the start of every session. Nothing in CI checks that it happened.
