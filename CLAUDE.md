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
`billing_runs.status` 'created', `email_broadcast_log.scope` 'all'/'leg');
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
| Produktionsleistung (% der Anschlussleistung, min. 5%) | `production_capacity_percent` |
| Produzent / Konsument (Messrichtung, nicht Person) | `producer_count` / `consumer_count` |
| LEG, BKW, Rappen, QR-Rechnung | unchanged (proper nouns) |

### Domain model core

A LEG also carries `production_capacity_percent` (plus the date it was
read): the installed production capacity as a percentage of the
participants' total Anschlussleistung. Art. 19e Abs. 1 StromVV requires
at least 5%, and BKW's LEG portal shows the current figure on every
metering point registration. It is copied in by hand and **cannot** be
derived — a site's Anschlussleistung is not in this database and cannot
be obtained. `app/domain/production_capacity.py` turns it into the number
that actually gets used: with production unchanged, the participants'
total Anschlussleistung may grow by `percent / 5` before breaking the
floor, which answers "does another consumer still fit in this LEG, or
does the next one wait in the pooled LEG until a producer signs up".
The 5% floor is law and a constant; where "getting tight" begins is
judgement and lives in `LegSettings.production_capacity_warn_percent`.
Percentages and factors are written German-style via `format_percent`/
`format_factor` so the same figure never appears two ways.

The BKW *discount* tier (40%/20% on the Netznutzung) is deliberately
**not** stored — the administrator knows it and does not need it
recorded. Do not restate it as derivable: BKW's criterion is the number
of **Netzebenen** the shared electricity crosses, confirmed by BKW per
location. Whether a LEG pools several substation areas
(`app.domain.leg_composition`) correlates with that but is not the same
statement, as migration 45's permanent description also says. A
`leg.discount_level` column existed briefly (migrations 45 and 46) and
was removed again.

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

**A page rendering is not evidence that it works.** Rendering every route
proves only that each page *builds*, never that a click does anything.
The `nicegui 3.15 → 3.16` bump (`2a33e40`, the Dependabot commit "Bump
the python-dependencies group with 7 updates", 2026-09-11) changed the
upload API from `event.name`/`event.content.read()` to
`event.file.name`/`await event.file.read()`; the email attachment handler
kept the old attributes and raised, so for nine days every broadcast went
out **without its attachment**.

Why nobody saw it is worth knowing, because it is this app's own defect
and it is now fixed. `app/main.py` registers `app.on_exception(
_handle_ui_exception)` precisely so a handler crash shows a red toast.
But NiceGUI runs a handler inside `with parent_slot:` and calls
`handle_exception` *outside* it (`nicegui.events.handle_event`), so for a
**synchronous** handler no slot remains: `ui.notify` raises `RuntimeError`
and `app/gui/safe_notify.py` swallows it. Async handlers are unaffected —
NiceGUI handles their exceptions inside the slot. `_handle_ui_exception`
therefore now enters a connected client's context itself before
notifying, and `safe_notify` logs its give-up branch at ERROR with a
traceback, so "we could not tell the user" is at least greppable.

So when bumping a GUI dependency or touching an event handler, drive at
least one real interaction per changed surface rather than stopping at
render. Where a handler reads framework objects, extract that part into a
module-level function so a test can call it (`read_uploaded_file` in
`app/gui/pages/email_dispatch.py`), and pin the framework's event shape
in a contract test (`tests/test_email_attachments.py`) so the next API
change fails a test instead of a real send.

Note also that `tests/conftest.py` has an autouse fixture pointing
`connection_scope()` at a throwaway database: without it, any test that
renders a page opens the real `data/leg_abrechnung.sqlite3` with actual
members' data in it.

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
