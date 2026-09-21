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

The key functions themselves live in `app/sort_keys.py`, which imports no
NiceGUI, and `app/gui/sorting.py` re-exports them — pages keep importing
them from there. The PDF layer groups a person's sites and has to put
them in the same order the Standorte page does, and it must not import
from `app/gui` (same reason `app/format_size.py` is layer-neutral). One
set of keys is what makes "the same order everywhere" true rather than
aspirational.

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
| Bezeichnung (frei, z. B. „Whg. 3. OG") | `MeteringPoint.label` |
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

### One customer, one document — the vZEV model

A participant is **one** customer of the LEG: one netted amount, one
reference number, one payment, one receivables account, one dunning
notice — however many sites and metering points they hold. This is the
model the administrator is themselves billed under as a vZEV operator:
the grid operator pays out one surplus or sends one invoice, and who owes
what inside the building is the operator's own problem. A property
management with several buildings is billed the same way.

So there is deliberately **no** per-customer billing logic: no
distribution key of their own, no split into several documents, no
setting that makes one participant's bill work differently. The moment
one customer gets their own rules, every quarter turns into a
negotiation about adjusting their invoice by hand.

What such a participant does get is detail. `app/pdf/bill_breakdown.py`
groups the quarter by site and metering point, and the document prints
each site's address, its Bezug and Einspeisung itemised per metering
point, and that site's own balance -- the figure they carry into their
own internal allocation. The grouping is presentation only: the amount
comes from `app/domain/billing.py` as it always did, from the person's
totals. `MeteringPoint.label` exists for the same reader, because
"CH1018…" tells nobody which flat it is.

**Every metering point the person held is on the document, including one
that shared nothing — then at 0.000 kWh.** A bill's line-up must not
change from quarter to quarter: a recipient who finds a meter missing
cannot tell whether it was deliberately excluded or forgotten. Which side
a meter is listed under follows its `direction`, never which of its
totals happens to be non-zero, so a PV meter that delivered nothing this
quarter stays in the Einspeisung section rather than vanishing. The same
reasoning gives everyone with an assignment a document, even one reading
0.00 CHF — `app.domain.distribution._seed_participants` enters every
participant of the quarter into the result at zero, and billing and the
PDF both simply fall out of that. The one figure withheld from a zero
document is the flat paper-invoice fee: charging 2 francs for a statement
reading 0.00 is not defensible, and a quarter with no local sharing would
otherwise become an invoice run for the fee alone.

Two things this made necessary, both of which had been latent:
`app/pdf/layout.py`'s table drawing now breaks across pages (no document
had ever held more than two energy lines, so it simply drew off the
bottom of the page and through the QR-bill's reserved area), and it
truncates a label that would collide with the kWh column. reportlab
never complains about either, so the column has to enforce its own bounds.

`SubstationArea` (BKW Trafokreis, physical) → `Site` (physical connection
site with an address) → `MeteringPoint` (a meter, consumption or feed-in
direction) → `Assignment` (time-bounded link from a metering point to a
`Person`, so a mid-quarter move splits readings automatically). `Leg` is
the billing group a metering point is assigned to — normally one per
substation area, but can span several if their owners pool together (the
app warns about this on several pages but doesn't compute the resulting
BKW discount itself). `BillingRun`/`BillingRunItem` are produced per LEG
per quarter from `app.domain.distribution.compute_quarter_distribution`.

### Exactly one current set of documents

Re-billing a quarter deletes the run and creates a new one, so its line
items get fresh ids — and the export used to write
`Abrechnung_Muster_7.pdf` next to the previous `..._1.pdf` and leave both
lying there, identical in every visible respect but the amount. That is
how a member gets sent the invoice from before a price correction. So
`app.pdf.export_service` removes the superseded documents, and three
details of that are deliberate: only files matching
`_GENERATED_PATTERNS` are touched (the folder may hold the
administrator's own notes), the removal happens *after* the new documents
are written (a half-failed export must not leave them with neither set),
and what was removed is reported in `ExportResult.removed_paths` rather
than done silently.

`create_billing_runs_for_all_legs` bills and exports every LEG in one
pass, because doing them one at a time is how a LEG gets forgotten —
nothing in the per-LEG flow says which ones are still outstanding. A LEG
that fails lands its message in its own `LegRunOutcome` and the rest
continue; `LegNotAssignedError` stays the one deployment-wide abort.

### Before billing, say what the quarter contains

`app.domain.statistics.quarter_energy_totals` answers the two questions
the app used to leave open: which quarter is worth billing, and did the
last import land. A quarter can hold a hundred thousand readings and
still be unbillable — the demo's winter quarter has 11'415 kWh of
consumption and **no feed-in at all**, so nothing can be shared and every
document reads 0.00. `QuarterEnergy.note` says so *before* the run, on
the Abrechnung page and in the Auswertungen table.
`missing_metering_points` compares metering points that held an
assignment against those that actually reported, using the same overlap
rule as the distribution, so a shortfall really does mean a partial
import rather than someone who moved out.

### Billing is a guided run, and the gate is not decorative

`app/models/billing_cycle.py` tracks a quarter's billing the same way
`person_onboarding` tracks a membership: a fixed `STEPS` list, one
optional date each, one row per quarter covering **every** LEG. The
billing page renders it through `app/gui/billing_cycle_view.py`.

Before it may be computed, `app/domain/billing_checks.py` has to pass.
The reason it blocks rather than warns is worth stating plainly: the
distribution splits each interval's shared energy among whoever is
present at that instant, so a metering point whose readings were never
imported does not merely go unbilled — its absence **enlarges everyone
else's share**, and the resulting invoices look entirely normal. One
forgotten file is therefore wrong invoices for the whole LEG.

Four checks, all blocking: every metering point has a LEG; every
assigned metering point delivered a full quarter of 15-minute values
(zero kWh is a statement, no data is not); assignments without overlaps
or gaps; and locally delivered equals locally drawn per LEG. That last
one is an engine invariant, not a measurement — `S(t)` is split equally
across both sides — so a difference only ever means energy could not be
attributed to anyone (`unassigned_kwh`), never that production exceeded
consumption. Surplus in either direction is settled with BKW and never
reaches this app.

Two things learned by running it rather than reading it:

- **The balance tolerance has to scale with the participant count.**
  Each person's totals round to three decimals independently, so a fixed
  0.001 kWh fired on perfectly sound data. Same reasoning as
  `verify_sum_balance`, which scales its Rappen tolerance with the item
  count.
- **A recorded check is not a promise about now.** The control points are
  recomputed on every page load and never stored; when they are red
  although step 2 carries a date, the page says the data changed since.
  A tick that no longer holds is worse than no tick.

The gate can be stepped past, because BKW may genuinely never deliver a
meter's data — but only via `record_override`, which refuses a blank
reason and keeps the text visible from then on. The control points stay
red afterwards: an override excuses proceeding, it does not repair
anything.

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

**Run the whole suite before a commit, not after every edit.** It takes
four to six minutes, and roughly half of that is `create_demo_data`
rebuilding 229'632 readings, once per test, 54 times over. While working,
run the test files the change actually touches
(`pytest tests/test_billing.py -q`) plus a render check when a page
changed; save the full suite and the four gates for the point where the
work is claimed to be done. Waiting six minutes to learn that a one-line
edit compiles is not verification, it is ceremony.

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

A page function's own tests are not enough either: they call it
directly, which never touches routing. `/backup` answered HTTP 422 for a
whole session because a helper had been inserted between `@ui.page(...)`
and the function it decorated, making the *helper* the route -- FastAPI
then demanded its argument as a query parameter, and the real page was
never registered. `tests/test_page_routes.py` checks the routing table
itself for exactly that.

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
