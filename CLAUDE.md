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

# Run the full test suite (across the machine's cores)
.venv\Scripts\python.exe -m pytest -n auto

# Run a single test file / test -- no -n here, see "Tooling and CI"
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
  send orchestration for broadcasts/invoices. One `sendMail` call per
  **contract party**, never CC/BCC: that is the privacy mechanism for bulk
  sends. A couple is one party with two addresses, and those two do share
  one message's `toRecipients` — what the rule forbids is two *different*
  parties in one message. It also means one outcome per party, so an
  invoice is never half sent.
- `app/domain/salutation.py` — the one `letter_salutation(person)` both the
  PDF and the email templates use, so a document and the mail announcing
  it cannot greet the same person differently.
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
goes behind every "Z…" name ("Ärni" last of all) — and it compares numbers
inside a name character by character, so "TRA19400" lands ahead of
"TRA9365". BKW's Trafokreis designations run three to five digits, which
put **all 34** of them in the wrong place, not some edge case. Build keys
from `text_key`, `number_key`, `address_key` (house numbers numerically)
and `person_name_key` (surname, falling back to the company name) rather
than hand-rolling per page — a person or an address must come out in the same
order on every page that lists it. Prefer negating a number in the key
over `SortOption.reverse`, which also flips the text tiebreak.

`text_key` is number-aware, via `natural_key`: it folds the text and then
splits it into alternating text and integer parts, so a number inside a
name sorts as a number on **every** list rather than only on the one whose
order was complained about. The Trafokreis is a sort key on its own page,
on the Standorte page and twice on the LEG detail page, so fixing one would
have left the app with two orders for one name. `MeteringPoint.label` gets
it for free ("Whg. 3. OG" before "Whg. 10. OG").

Two properties of that key are load-bearing rather than incidental.
`re.split` with a capturing group always yields an odd-length list starting
and ending with a (possibly empty) text part, so **even positions are
always a `str` and odd ones always a `numeric_part`** -- without that
guarantee two keys can compare a number against a str and the page raises
`TypeError` on one deployment's data. Any new branch that returns a key by
hand has to go through `text_key` for the same reason;
`person_name_key`'s missing-person case returns `text_key("", "")`, not a
bare `("", "")`.

And the numeric part is `(length, digits)`, deliberately **not** `int()`.
Since Python 3.11 converting more than 4300 digits raises `ValueError`, and
these keys are not built only from what the administrator typed:
`app.importers.cloudflare_client` takes names and addresses straight from
the leg-ittigen.ch web form, which caps nothing, and the Webanmeldungen
inbox sorts them. `address_key` had this exposure on the house number
before `natural_key` existed, so one submission could stop that page
rendering. Comparing `(length, digits)` needs no conversion and is exactly
as correct: with leading zeros stripped, more digits *is* a larger number,
and equal-length runs compare the same way as text and as numbers.
Stripping the zeros is why "TRA007" and "TRA7" tie, which `sorted` resolves
by stability.

A dropdown is the exception that still needs care: it has no "Sortierung"
control, so the order it is built in is the only order there is. The
Trafokreis select in `app/gui/site_form.py` and the one-sided-Trafokreis
warnings in `app.domain.quality_checks` therefore sort explicitly instead
of inheriting `substation_area.list_all`'s `ORDER BY name`.
`tests/test_site_form.py` drives the dialog rather than the key function,
because a unit test on `text_key` passes whether or not anybody calls it
there. Person
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

A finished offboarding is not a finished job. Completing the last step
offers to remove the person, but the offer comes once, and the finished
tracker used to drop off the Austritte worklist while the person stayed
active — counted on the dashboard, offered for new assignments, still on
the broadcast list. That is how a fully offboarded member was found still
listed weeks later, by eye. So `check_offboarding_completed_but_active`
puts them on the dashboard, and the Austritte page keeps the card visible
(badged "Person noch aktiv", removal one click away) until it is settled.
Deliberately *not* folded into `person_offboarding.list_in_progress`: the
Debitoren page reads that as "Austritt läuft", which this is not.
Deactivating now stamps `Person.deactivated_at`, so a list can say "Inaktiv
seit 13.09.2026" — `None` for anyone deactivated before migration 50, and
no date is invented, because a made-up one prints as though it were
recorded.

### Genossenschaft membership: dated, like an Assignment

`app/models/cooperative_membership.py` is `Assignment`'s shape applied to
membership: one row per period, `shares`, `valid_from`/`valid_to`,
`covers()`. A share count is *not* a column on `person`, because a
cooperative has to answer "who held how many shares when" years later —
changing the count closes the running row and opens a new one. Two
deliberate differences from `Assignment`: a **gap is legitimate** here
(leaving and rejoining), so `find_warnings` reports only overlaps; and
membership is judged **strictly on today** (`covers(date.today())`), not
`is_current_or_upcoming`, because mailing "die Genossenschafter" must not
reach somebody who has not joined. Zero shares is allowed — the membership
is a fact while the paperwork lags — but
`check_cooperative_members_without_shares` makes sure it is not forgotten.
The members' list is the Personen list with "Nur Genossenschafter" on and
printed; there is no page of its own.

**Where it is edited follows the icons, and that is not negotiable.** The
eye opens a view, the pencil opens an edit dialog. Joining, leaving and
buying shares are changes, so the controls live in
`app.gui.person_form` (`CooperativeEditor` in `app.gui.cooperative_form`)
and the detail page only renders `render_cooperative_history` -- no
buttons at all. The first build had it the other way round and the
administrator could not find it, which is the correct verdict on it.
Three plain controls (member yes/no, how many shares, from when) produce
the period bookkeeping, and the date means **one** thing in all three
actions: the day the new state takes effect. A changed count closes the
running period the day before and opens the next; a cleared checkbox does
the same, ending the period the day *before* the exit takes effect; a
correction **on** the start day overwrites that period instead of creating
a zero-length one; and deactivating on the day membership began deletes it,
because it would cover no day at all. A date before the running period is
refused rather than silently producing an overlap.

The exit used to be the odd one out, writing `valid_to` = the given day.
`covers()` includes that day, so somebody removed today stayed a member for
the rest of it -- badged in the list, in the filter, and on the members'
mailing. The administrator found it within minutes of first use
(aktivieren, 10 Anteile, deaktivieren). Corrections and deletions made in
the dialog commit immediately, so the editor re-reads its state and tells
the calling page -- otherwise the list keeps its badge even when the dialog
is closed with Abbrechen.

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
| Genossenschaft / Genossenschafter | `CooperativeMembership` / member |
| Anteile | `shares` |
| Bemerkung (intern, Person) | `Person.note` |
| Zweite Person (Paar) | `second_first_name`, `named_persons` |
| Briefanrede | `letter_salutation` / `{briefanrede}` |
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

The module used to go one step further and **recommend** moving people out
of a pooled LEG into a dedicated one, once a Trafokreis had both sides and
enough people. That is gone, along with `find_upgrade_candidates`,
`leg_should_split` and `check_leg_upgrade_potential`, because presence is
not viability: a Trafokreis with seven feed-in meters and one consumption
meter passed the test, and following the advice would leave the producers
with nobody to share with. A real administrator had parked a 34 kWp
producer in the pooled LEG for exactly that reason and was told to undo it.

A ratio threshold would have been the obvious repair; it was deliberately
**not** built, because the decision turns on economics, on what the
participants will agree to, and on what BKW confirms per location — none of
which is in this database. What the LEG detail page shows instead is the
one fact that is: per metering point, whether its Trafokreis already has a
LEG of its own (🟢 with that LEG's name — switch the row over) or would
need one founded first (🟠). Shown only on a LEG that spans several
Trafokreise, since on a dedicated one the answer is itself. The green dot
has a second use nobody planned: it appears exactly when a migration was
left half done and a meter stayed behind. `leg_settings.
leg_founding_min_persons` survives as an unread column — old migrations are
never rewritten — and no longer appears in the settings form.

Kept, because it is a fact and not advice: `check_substation_area_one_sided`
("nur Produzenten"/"nur Konsumenten" — nothing can be shared there at all).

The same question came back as "which LEGs have a good distribution and
which a bad one", and the answer built for it is
`app.domain.statistics.leg_balance` behind `/statistics/balance`. It reports
and **grades nothing**: the administrator was offered a threshold in
`LegSettings` (the pattern `production_capacity_warn_percent` already sets)
and chose facts instead, so there is no cutoff, no colour and no verdict
word — `test_the_view_grades_nothing` pins that, because the next person to
read the request would reasonably add one.

What stands in for the verdict is the **ordering**: feed-in meters per
consumption meter, descending, so one continuum runs from production-heavy
through balanced to consumption-heavy and both extremes are where the eye
lands first. A LEG with no consumption meters has no quotient and leads; an
empty LEG is neither end and is pushed past every populated one, named
rather than hidden, since a LEG nobody assigned anything to otherwise looks
exactly like one that does not exist.

The meter counts are only a **proxy** and the view says so by carrying the
real figure beside them: `shared_kwh / feed_in_kwh`, how much of what was
fed in actually found a taker. Nine feed-in meters beside twenty-six
consumption meters says nothing about whether the sun shone while anybody
was drawing. That figure needs an import, so today it reads "—" —
deliberately not "0 %", because "nothing was produced" and "what was
produced found no taker" are different statements and only the second is a
problem. `shared_energy_by_leg` forms it per 15-minute interval **and per
LEG** before summing anything, the same rule the invoices use; both halves
have their own test in `tests/test_leg_balance.py`, because each produces a
plausible-looking wrong number on its own.

`leg_balance` takes its meter counts from `distribution_by_leg` rather than
counting again, so the Verteilung view ("how big is each LEG") and the
Ausgewogenheit view ("is each LEG matched") cannot disagree about the same
LEG's size.

`compute_participant_roles` answers a different question from
`ParticipantMix` and must not be confused with it: how many **people** of
each kind, counted once each, for the overview tiles. `ParticipantMix`
counts somebody with both directions on both sides on purpose (the question
there is whether a supply and a demand side exist at all); the roles do not
overlap and add up to a real headcount.

The split follows the administrator'''s model of the LEG: whoever feeds in
also draws at that address, so the feed-in side **is** the Prosumer side --
which is why the tile reads "Prosumer" for everyone who feeds in, including
the few with no consumption assignment. Those are not a third kind but a
gap, and `check_feed_in_without_consumption` names them. Deliberately
one-directional: drawing without feeding in is the normal case (no PV), and
warning about it would put a notice on most of the membership and train the
administrator to ignore the list. The message asks rather than asserts --
whether BKW supports feeding in without drawing is not something this
database can establish, the same reasoning that keeps the discount tier
out.

### One customer, one document — the vZEV model

A participant is **one** customer of the LEG: one netted amount, one
reference number, one payment, one receivables account, one dunning
notice — however many sites and metering points they hold, **and however
many people they are**. A couple is one `Person` carrying two names
(`second_first_name`/`second_last_name`, migration 50), not two records:
two records would mean two invoices for one household and a distribution
key to argue about. Exactly two, deliberately — a third name would need a
sub-table, not a third set of columns.

Both partners are contract parties, so both are named on the address block
(one line each, salutation in front of the name), both are greeted by
`app.domain.salutation.letter_salutation`, and both email addresses go into
the one message addressed to them (`Person.contact_emails`). The greeting is
"Guten Tag …" on purpose: German adjective inflection has to agree with
gender, and every earlier mechanism got that wrong — the PDF printed a fixed
"Sehr geehrte Kundin, sehr geehrter Kunde" naming nobody, and the email
templates let "Sehr geehrte {anrede} {nachname}" render as "Sehr geehrte
Herr Muster". "Guten Tag" carries no adjective, so one rule covers one
person or two, any salutations, and none at all. **An empty salutation is a
valid state, not a defect.** Use `{briefanrede}` in a template; the older
`{anrede}`/`{nachname}` remain only because they are in the
administrator's saved texts.

One boundary this exposed: the Swiss QR-bill limits the payer name to 70
characters, and `qrbill` raises for a longer one — which used to surface as
"check the QR-IBAN and sender address" and cost the whole document. A
couple's name that overruns falls back to the first person
(`app.pdf.qr_bill_render.qr_debtor_name`) and says so in
`ExportResult.errors`. The address block still names both. This is the
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

`requirements-dev.txt` adds ruff, bandit, pip-audit and pytest-xdist on top
of `requirements.txt`; `pyproject.toml` holds their configuration. `pytest`
itself is in `requirements.txt` because `start.bat` installs only that one,
but its parallel runner is not: xdist pulls in execnet, and the
administrator's virtualenv has no reason to carry a remote-execution
library. `pytest -n auto` therefore needs the dev file installed.
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

**Run the whole suite before a commit, not after every edit**, and run it
as `pytest -n auto`. Two rounds of work got it from 8:27 to about 2:20:

- `create_demo_data` rebuilding 229'632 readings, once per test, 54 times
  over, was roughly half the runtime. `tests/conftest.py` now builds it
  once per session into a template and restores that with SQLite's
  `backup()` for each test; `real_demo_data` marks the few tests that want
  the genuine article. 8:27 → 5:53.
- `pytest-xdist` then splits what is left across the cores, roughly 5:53
  → 2:20. Less than the core count would suggest, because each worker is
  its own pytest session and so builds the template for itself — the two
  optimisations overlap, and that is the price of the first one.

**Treat a single timing as noise.** Four full runs on the same machine
(4 physical cores, 8 logical) came out 2:03, 2:16, 2:28 and 2:45, and the
spread within one worker count was as wide as the difference between four
workers and eight. A run that looks slow is not evidence of a regression;
measure twice before believing it, which is advice this file earned by
getting it wrong once. `-n auto` resolves to eight here rather than four
because it means *physical* cores only when `psutil` is installed and
falls back to `os.cpu_count()` otherwise — pinning `psutil` to change that
would buy nothing measurable and add a compiled dependency to a desktop
app.

`-n auto` is deliberately **not** in `pytest.ini`'s `addopts`. On the one
file you are actually working on, xdist's process startup costs more than
it saves, and it swallows `pdb` and `print`. Full run parallel, targeted
run plain. While working, run the test files the change actually touches
(`pytest tests/test_billing.py -q`) plus a render check when a page
changed; save the full suite and the four gates for the point where the
work is claimed to be done. Waiting for all 905 tests to learn that a
one-line edit compiles is not verification, it is ceremony.

Tests must therefore stay **order- and process-independent**: xdist hands
each worker an arbitrary slice. Nothing may rely on another test having
run first, and a name registered globally -- a NiceGUI probe route
(`ui.page("/probe-...")`) above all -- has to be unique across the whole
suite, not merely within its file, because two files that never shared a
process before now can.

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

### The official address register, and the locality rule

`app/importers/address_register.py` downloads swisstopo's **Amtliches
Verzeichnis der Gebäudeadressen** on one click and builds
`data/adressregister.sqlite3`; `app/domain/address_lookup.py` narrows it
while typing and checks a finished address. Four places take an address and
all four use it: `app/gui/site_form.py`, `app/gui/person_form.py`,
`app/gui/pages/settings.py` (the LEG's own sender address -- the creditor on
**every** QR-bill, see `app/pdf/qr_bill_render.py`) and
`app/gui/pages/web_registrations.py`, which checks a stranger's typing
before it is adopted.

**Swiss Post's address data was rejected, not overlooked.** Its licence
forbids passing the data on -- `swissmatch-location` had to stop shipping it
after a licence change -- so it can never be part of a published
application. swisstopo's register is free for commercial use *and*
redistribution; naming the source is the only condition, which the
Adressregister page does.

**Downloaded whole rather than queried, and that is a privacy decision.**
geo.admin.ch offers a free fuzzy-search API over the same data. Using it
would send fragments of a member's address to a federal server on every
keystroke in an address field. With the register on disk, no address ever
leaves the machine.

**A file of its own, never tables in the member database.** Three million
rows of third-party data have nothing to do with the members, every backup
would grow by well over a hundred megabytes although the file can be
re-downloaded at any time, and the schema migrations never have to touch it.
`PRAGMA synchronous = OFF` during the build is defensible for exactly that
reason and must not spread to the member database.

Four decisions were measured, and each of them is the opposite of the
obvious choice:

- **Do not filter `ADR_OFFICIAL = true`.** It reads like the right filter
  and loses real addresses: of 92 sites in the live deployment, 86 validated
  against the whole register and only **83** against the official rows.
  `official` and `status` are carried as flags and only ever influence the
  *order* suggestions appear in. `status = 'planned'` is kept for the same
  reason.
- **Compare the house number as folded text**, never split into a figure and
  a letter the way `address_key` does. Of the official addresses 331'401 are
  dotted ("31.1"), 25'403 are shaped differently again and 7'078 are empty.
- **The locality is always the postal one** (`ZIP_LABEL`), never the
  political municipality (`COM_NAME`). It is the name the member reads on
  the invoice, and somebody living in Worblaufen should not find their
  village replaced by the municipality that absorbed it. It is also the
  better-defined of the two: postal code 3048 lies in **two** municipalities
  (Ittigen and Bern), so the political name is not even determined by the
  postal code. A stored municipality is accepted rather than called wrong,
  and the postal locality is offered -- `COM_NAME` is never filled in.
- **`Site.municipality` therefore holds a postal locality despite its
  name.** The German label is "Ort" everywhere; the column keeps its name
  because renaming it is a migration through `from_row`, every query, the
  sort keys, the PDF and the tests, which is a lot of movement for a word.
  Do not "fix" the data to match the identifier.

**A street that exists elsewhere is a wrong postal code, not a wrong
street**, and finding that out cost a real run against the real data. Asked
for the closest street *within* the typed postal code, the register offered a
correctly spelled street the name of a **different** real street, and one
click would have written it into the record. Almost every Swiss street name
ends in "strasse", so the shared suffix alone carries the similarity score --
invented but identical in shape, "Rosenstrasse" against "Nelkenstrasse"
scores 0.720. A higher threshold cannot separate the cases: the real bad
suggestion scored 0.733 while a genuine postal-code-in-the-locality-field
error scores 0.737.
So `verify` first asks whether the street exists under another postal code
and reports `FIELD_POSTAL_CODE` if it does, and the locality check is then
skipped -- comparing against the localities of a postal code that is itself
the mistake would report two findings for one error. Streets additionally
use a stricter cutoff (`_CUTOFF_STREET`) than localities, whose candidates
are the few names behind one postal code and genuinely dissimilar.

Which field a "Ja" writes is an **explicit mapping** in
`app/gui/address_hints.py`, not an if/else: a postal-code finding landed in
the street field the first time, which is exactly what "everything that is
not the locality is the street" invites.

**One hint, one wording, yes or no.** "Meinten Sie: Worblaufen?" with Ja and
Nein, and nothing else -- no severity, no explanation of why the app is
asking. A typo, the political municipality and a PO box all look the same on
screen; the difference stays inside `address_lookup`. Where the register
holds nothing close enough, the line is "Nicht im amtlichen Verzeichnis."
with only a Nein.

**The overview summarises.** `summarise_warnings` collapses several
findings of one kind into a single counted line -- thirteen address lines
pushed everything else off the screen, and a list that long stops being read
at all. A *single* finding keeps its own message, because naming the one
metering point without a LEG is more useful than "1 Messpunkt ohne LEG" and
costs the same line. Grouped by category and `summary`, never by `link`:
most per-record warnings link to their own record, so grouping by link would
collapse nothing; the collapsed line carries `summary_link`, pointing at the
list where the whole group can be worked off. A check with no `summary` is
never collapsed, so this is opt-in per check.

**The question is asked at the field, and only there.** The first build put
it in a card above the Standorte and Personen lists, reasoning from the
Austritte page, which does carry its one action on the worklist. That
reasoning was wrong: Austritte shows *what* is being removed, while this
showed a name and a suggestion with no sight of which field was meant or
what stood in it. The administrator's verdict -- "ich sehe ja gar nicht bei
was?" -- is the correct one, and the card is gone. The lists now render a
warning triangle beside the eye (`issue_ids`), which says "look at this one"
and claims nothing more; the dashboard states the fact and links there; and
`app/gui/address_input.py` renders the question directly under the row
holding the value it would replace. The triangle is sized at `1.715em`
because a bare `q-icon` inherits the surrounding `1em` and comes out
visibly smaller than the icons in the flat buttons beside it. The Personen
list also has a "Nur fehlerhafte Adressen" switch, so the marked handful can
be worked off without scrolling ninety cards.

A "Nein" taken in a dialog is collected in `SuggestionBox.dismissals` and
written by `store_dismissals` **after** the record is saved -- a new record
has no id while the dialog is open.

**`verify` says nothing until there is an address to check.** With a blank
street or postal code it returns nothing, and a blank house number is
skipped rather than reported: those are "not typed yet", not "wrong". A
freshly opened Standort dialog otherwise greeted the administrator with
"Nicht im amtlichen Verzeichnis." under an empty field -- a complaint about
something they had not written, sitting exactly where they were looking for
help, which is what made the suggestions look broken. Every check needs the
postal code anyway, so without one there is no honest statement to make.

`tests/test_site_form_address.py` drives the **dialog**, setting a field's
value the way typing does. The tests in `test_address_hints.py` call
`SuggestionBox.update()` directly, which proves the logic and nothing about
the wiring -- and the empty-form complaint slipped through exactly there.

The "Nur fehlerhafte Adressen" switch on the Personen list is hidden while
nothing is marked, and switches itself off when the last finding goes: a
control that can only ever empty the list is clutter, and leaving it on
after the last correction would filter the list down to nothing from
off-screen.

**The suggestion list floats over the form** (a `ui.menu` anchored to the
field, dismissable with Escape) rather than sitting in the layout, where it
resized the dialog on every keystroke -- distracting at exactly the moment
the administrator is reading what they type. And a postal code already in
the form **ranks** the street suggestions: without that, typing a street
with "3063 Ittigen" filled in offered six streets from other cantons above
the one that fitted. Ranked, not filtered, because a street really can sit
behind a different postal code -- that is what `FIELD_POSTAL_CODE` reports,
and filtering would make the correction unreachable.

**Nein stores the confirmed value** (migration 51:
`site.address_confirmed`/`locality_confirmed`,
`person.billing_address_confirmed`/`billing_city_confirmed`), not a flag and
not a date. A date would keep silencing a hint after the address beneath it
changed, and a tick that no longer holds is worse than no tick -- the same
reasoning that keeps the billing control points unstored. With the value the
dismissal expires by itself. Two columns per record because the
street/house-number finding and the locality finding are independent, and
`update()` deliberately does **not** write these columns, so editing an
unrelated field cannot clear a dismissal.

Persons are checked only when `billing_country` is CH, but then **fully**,
street and house number included. That was a reversal: sparing the
occasional PO box one click is not worth leaving every ordinary typo in a
billing address unchecked, and one Nein retires a PO box for good.

**The update must not block and must be followable after leaving the page.**
The pattern in `app/gui/pages/import_page.py` yields *between* files and
lets each file's work block, which is fine for many small files and useless
for one 35-second parse -- the bar would sit at zero and the window would be
dead. So the download streams with `httpx.AsyncClient` (already a
dependency, already used in `app/emailing/graph_client.py`) and
`build_register` is a **generator** that hands control back every 5'000
rows. Progress lives in a module-level object in
`app/gui/address_register_task.py`, not on a client, and
`app.gui.navigation.page_frame` shows it in the header of all 21 pages;
state on the page that started it would vanish the moment the administrator
navigated away, which is the whole thing being fixed. A second click finds
the phase set and returns.

Yielding is not enough on its own, and measuring showed where. The read loop
hands back control every 46 ms (95th percentile 55 ms), but one block ran
**2'070 ms**: `CREATE INDEX idx_address_lookup` over 3.3 million rows, a
single SQLite statement that cannot be broken up from Python. That is past
the one second NiceGUI allows the browser to answer a state query, and it
filled the log with `TimeoutError: JavaScript did not respond within 1.0 s`
while an update ran. So `build_register` stops before indexing and
`finalise_register` does the indexing and the swap, run with
`asyncio.to_thread` -- SQLite releases the GIL while it works, so a thread
is all it takes. Measured again afterwards: longest block 179 ms, worst
delay to a waiting task 243 ms.

Measured by running it: 76 s for the whole click (16 s download of 143 MB,
then the parse of 3'303'418 rows), and **148 MB** on disk -- 358 MB if
street, locality and municipality were repeated per row instead of held in a
`street` table. The 127 MB measured before the build existed was without
`locality_fold` and its index, which the locality search needs so that
umlauts fold the way they do everywhere else.

The download URL is read from the STAC catalogue and then checked against
`_ALLOWED_HOSTS` over HTTPS. A forged catalogue response is needed to
exploit it at all, so that check bounds the damage rather than closing a
hole -- but this is the one path in an otherwise offline app that fetches
from the network and writes to disk, and the set of legitimate hosts is
three entries long.
Quarterly staleness (`STALE_AFTER_DAYS`), shown as the **data** date from
STAC rather than the download time. The asset URL is read from STAC too,
because swisstopo versions the file name.

**Without a register nothing breaks**: no suggestions, no findings, and the
Adressregister page is the one place that says it has not been downloaded.
The lists stay silent about it. `tests/conftest.py` has an autouse fixture
pointing the register path at a file that does not exist, for the same
reason it redirects `connection_scope()`: otherwise a test that renders the
Personen page would read whatever register the developer's machine happens
to hold. That fixture only works because the path is resolved in the
function body -- a default argument would bind it at import time.

### The repository is public, and that is a deliberate trade

`schopf16/leg-abrechnung` is **public**, so that the free GitHub tooling
(CodeQL, secret scanning, Dependabot, Actions) is available without paying
for it. The consequence has to be present before **every** commit: whatever
is committed is world-readable, immediately and permanently -- a later
`git rm` does not remove it from the history, and the history is what
people clone.

So nothing that identifies a member ever goes in: no name, address, IBAN,
email or customer number, not in code, not in a test fixture, not in a
commit message, not in a PR description. `.gitignore` covers `data/`,
`backups/`, `logs/`, `output/`, `*.sqlite3`, `config.local.*` and `.env`,
which is the mechanism -- but the check before committing is the habit.
Test data is invented (`example.invalid` addresses, `Muster`/`Beispiel`
names); real figures quoted in a commit message stay aggregate ("26 von 29
Messpunkten"), never per person.

Licensed **GPL-3.0-or-later** (see `LICENSE` and README section 10): others
may use and adapt it, and anyone distributing a modified version has to
publish their source too, so improvements can find their way back. GPL-2
was ruled out because `svglib` is LGPL-3.0 and incompatible with it; every
other dependency is MIT or BSD.

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
