# CLAUDE.md

Guidance for Claude Code in this repository. **Rules only** — the reasoning
behind them was removed deliberately (it cost ~24k tokens at every single
step of every session). Where a rule looks arbitrary, it is the scar of a
real defect; follow it rather than re-deriving it.

## What this is

Desktop app (German UI, English code) for the administrator of a Swiss
"lokale Elektrizitätsgemeinschaft" (LEG) in BKW's grid: import meter
readings, distribute shared solar production at 15-minute resolution, issue
quarterly invoices as PDFs with a Swiss QR-bill. Fully offline: NiceGUI +
pywebview native window over local SQLite. `README.md` (German) is the
user-facing description.

## Commands

```
.venv\Scripts\python.exe run.py            # run the app (or start.bat)
pytest tests/test_billing.py -q            # while working: the file you changed
pytest -m "not heavy" -n auto              # almost everything, no demo data
pytest -n auto                             # before a push
```

The four gates, all must be green before a push (no typecheck step):

```
.venv\Scripts\ruff.exe check app tests run.py
.venv\Scripts\ruff.exe format --check app tests run.py
.venv\Scripts\bandit.exe -q -r app -ll
.venv\Scripts\pip-audit.exe -r requirements.txt
```

`.venv` is gitignored, set up by `start.bat`. `requirements.txt` pins exact
versions; `requirements-dev.txt` adds ruff, bandit, pip-audit, pytest-xdist.
`pytest -n auto` needs the dev file. `-n auto` is deliberately **not** in
`addopts`: on one file xdist costs more than it saves and swallows
`print`/`pdb`.

## Working economy (read this first)

Cost is `context × steps`, so a long session is quadratically expensive.

- **One task per session**, `/clear` between. No multi-stage unattended runs.
- **Never read a whole file when `grep -n` or `sed -n` answers the question.**
- **`Edit` directly.** No Python patch scripts, no heredocs that rewrite
  files — they fail on backslash mangling and cost a full round trip each.
- **Do not re-read a file to verify an edit.** Edit fails loudly.
- **Short output.** Commit messages one subject line plus at most three
  bullets. Reports: what changed, what was measured, what is left.
- **Docstrings are one line.** No `Args:`/`Returns:`/`Attributes:` sections —
  the signature says it. Module docstring: one sentence, up to three lines if
  it carries a rule. Inline comments only where the *why* is invisible.
- **No new `.md` files.** Conventions go in this file, nowhere else.
- Full suite + gates once, before the push. Tests cost wall-clock, not tokens.

## Architecture

Strict layering, don't blur it:

- `app/models/` — one module per table, pure CRUD, no business logic. Each
  row dataclass has `from_row(sqlite3.Row)`.
- `app/domain/` — business logic. No SQL, no NiceGUI.
- `app/gui/pages/` — one module per `@ui.page("/...")`, registered by the
  side-effecting import of `app/gui/pages/__init__.py` in `app/main.py`.
  No SQL, no business rules.
- `app/importers/` — EBIX/CSV readings, camt.053/054 statements. Each format
  isolated behind a parsed-record dataclass (`ParsedReading`,
  `ParsedBankTransaction`).
- `app/pdf/` — PDF/QR-bill (`qrbill`, `reportlab`, `svglib`, `pypdf`) and CSV
  export.
- `app/emailing/` — Microsoft Graph (not SMTP; Basic Auth is off for Exchange
  Online), placeholder substitution, send orchestration.
- `app/db/` — `migrations.py`, `schema.py` (`initialize_database`),
  `connection.py` (`connection_scope()`).
- `app/backup/` — backup/restore via SQLite's online backup API.

Layer-neutral helpers (no NiceGUI import, because `app/pdf` must not import
from `app/gui`): `app/formatting.py`, `app/sort_keys.py`, `app/format_size.py`,
`app/domain/salutation.py`.

**One `sendMail` per contract party, never CC/BCC** — that is the privacy
mechanism for bulk sends. A couple is one party with two addresses and does
share one message's `toRecipients`; two *different* parties in one message is
what is forbidden. One outcome per party, so an invoice is never half sent.

`app/domain/salutation.py`'s `letter_salutation(person)` is used by both the
PDF and the email templates, so a document and the mail announcing it cannot
greet the same person differently.

### Database access

Every write path goes through `connection_scope()` (commits on clean exit,
rolls back and re-raises on exception). Repo functions in `app/models/` also
commit themselves by default; several take `commit: bool = True` so a bulk
caller can pass `commit=False` and let the enclosing scope commit once.

### Migrations

`MIGRATIONS` in `app/db/migrations.py` is the only way the schema changes:
append `Migration(version=last+1, ...)`. **Never renumber or edit an existing
entry** — old backups are migrated forward step by step, so history must stay
replayable. Unused columns stay (`leg_settings.leg_founding_min_persons`).
Old migrations keep their original German SQL forever.

Before applying a new migration to `data/leg_abrechnung.sqlite3`: test it
against a scratch copy, then `app.backup.backup_service.create_backup()`,
then for real.

### Money and energy

All monetary amounts are integer **Rappen** (`*_rappen`), never float or
Decimal in DB or domain. `Decimal` appears only at the PDF boundary for the
QR-bill library. kWh stay full-precision floats and are rounded to the Rappen
**exactly once**, at the final net settlement — never re-rounded and re-added.

`app/models/account_entry.py`: positive = person owes the LEG, negative = LEG
owes the person; an incoming payment is stored negative. **The GUI negates
this exactly once, where the Saldo is displayed** — never earlier, never in a
model or domain function.

### "Frozen at the time of the action"

Rates and values are copied onto a row when something happens, so a later
`LegSettings` change cannot retroactively alter what was already
communicated. On `BillingRunItem`: `price_rp_per_kwh`, `admin_fee_*`,
`due_date` (printed verbatim on re-export, never recomputed),
`dunning_deadline_days`. Default to this pattern for any new
settings-driven value that gets billed or communicated.

## Conventions of the interface

### Sorting: one mechanism, every list

Every browsable list sorts through `app/gui/sorting.py` and nothing else: a
module-level `SORT_OPTIONS` (default first), `bar.sort(SORT_OPTIONS, ...)` on
the page's `FilterBar`, and `apply_sort(rows, SORT_OPTIONS, sort_select)`.
`bar.sort` returns a `SortControl` (select + direction arrow) — pass the whole
control to `apply_sort`/`sort_description`, never just `.value`. Calling
`render_sort_select` directly fails
`test_no_list_page_lays_its_own_filter_row_out_any_more`; the one exemption is
the metering-point sub-table on the LEG detail page.

**Never Quasar's `sortable: True` headers.**

**Sort in Python on loaded rows, never in `ORDER BY`**: SQLite's collation
mis-sorts umlauts and compares numbers character by character. Keys come from
`app/sort_keys.py`: `text_key`, `number_key`, `address_key` (house numbers
numerically), `person_name_key` (surname, falling back to company). Prefer
negating a number in the key over `SortOption.reverse`, which also flips the
text tiebreak.

`text_key` goes through `natural_key`, which splits folded text into
alternating text and integer parts. Two properties are load-bearing: **even
positions are always `str`, odd ones always `numeric_part`** (otherwise two
keys compare int against str and the page raises `TypeError`), and the numeric
part is `(length, digits)`, **not `int()`** — since 3.11 converting >4300
digits raises, and `app.importers.cloudflare_client` takes uncapped names
straight from the web form. Any new key must go through `text_key`;
`person_name_key`'s missing-person case returns `text_key("", "")`.

A dropdown has no sort control, so the order it is built in is the only order
there is — `app/gui/site_form.py`'s Trafokreis select and
`app.domain.quality_checks`'s one-sided warnings sort explicitly rather than
inheriting `ORDER BY name`. Test the dialog, not the key function.

Person lists default to `last_name`. A worklist may lead with its own urgency
order (`dunning.py`), an inbox with newest-first (`web_registrations.py`), a
list with one sensible order shows no control (`signatures.py`). Exempt, no
control: detail sub-tables and history tables (`billing.py`, `backup.py`,
`import_page.py`, `reports.py`, `dashboard.py`, the metering-point tables on
the Person and Standort pages). The selection is printed on the printout's own
"Sortierung:" line via `render_print_button`'s `get_sort_description`, never
folded into the filter line.

### The drawer

`NAV_GROUPS` in `app/gui/navigation.py` is the administrator's year: Übersicht
→ Stammdaten → Vorgänge → Abrechnung → Statistik → Kommunikation →
Einstellungen. Auswertungen comes **before** Rechnungslauf.

**Exactly one chapter open**, via Quasar's `group=leg-nav`. The open chapter
*is* the answer to "which part of the app am I in". Entries keep `ui.link` but
lose its colour and underline; the open one carries a grey bar across the full
drawer width. Chapter headings stay black. The settings page is
**"Allgemein"**, because "Stammdaten" is the chapter.

### The filter bar: the page says what, the bar says where

```python
bar = FilterBar("/persons")
search_input = bar.search("Name, Firma, Adresse")
sort_select = bar.sort(SORT_OPTIONS, lambda: apply_filter())
show_inactive = bar.filter("Deaktivierte Personen anzeigen")
problem_filter = bar.problem_filter(lambda: apply_filter())
```

Wiring stays with the page; the bar owns layout only. Two columns: search and
sort left, filters stacked right. A conditional filter renders last whatever
order the page asks for it in. A filter's text is clickable via Quasar's
`label` **prop** — so in tests a switch's caption is in `_props["label"]`, not
`.text`. **No filter carries a count** (`test_no_filter_carries_a_count`). The
search field's label is "Suche" and the field list is its `hint`. Printing is
an action, not a filter.

`FilterBar.is_filtering()` compares a cleared input against `""` (Quasar's
`clearable` sets `None`). `FilterBar.reset(then)` puts every control back and
calls the page **once**.

### The keyboard: one dispatcher, a stack of who owns it

`app/gui/keyboard.py` holds one `ui.keyboard` per page (created in
`page_frame`) and a stack of `KeyboardLayer`s; whoever is on top answers.

| Key | Meaning |
|---|---|
| Enter | take the marked thing |
| ↑ ↓ ← → | move the mark |
| Escape | go back one step |
| Tab | next field — the browser's own, never intercepted |

Element bindings do not work: a Quasar dialog renders its card in a portal and
a lookup menu floats with `no-focus`, so neither holds focus. `ignore=[]` is
deliberate — NiceGUI otherwise drops keys from inputs and buttons.

**A layer claims only what it can answer.** A form dialog does not claim the
arrows (they stay caret movement); an address list and the discard question
do, while they are on top. `on_typing` is why this is a stack rather than a
flag: a `debounce`d field's value has not reached the server yet, and "a key
was pressed" needs no round trip.

The stack lives on the client, with a module-level fallback for code outside a
request. `tests/conftest.py` empties it around every test (autouse) and offers
the `press` fixture — pressing a key in a test goes through the dispatcher.

### A dialog must not be able to lose what was typed into it

`form_guard(dialog, on_save=...)` from `app/gui/form_dialog.py`, applied to
every dialog holding typed-in data, once, after its body is built and before
`dialog.open()`:

- **`persistent`** — a click beside the card does nothing.
- **Escape closes, but asks first when something was typed.** Dirtiness is
  snapshotted from every `ValueElement` inside the dialog. Limit: a field
  created *after* the guard is not in the snapshot.
- **Enter saves, from a single-line input only.** This one stays an element
  binding — the dispatcher cannot see which element has focus, so it could not
  tell a textarea from a one-line field. It skips any field carrying a lookup
  menu, where Enter belongs to the list.

**Enter is wired per action, not per dialog.** Pass `on_save` where the
primary action stores a record; leave it out where it sends mail, bills a
quarter, starts an exclusion or records a billing override. Those keep
`persistent` and the Escape guard.

The discard question is itself `persistent` (the Escape keydown that opens it
would otherwise close it), Escape in it means "Weiter bearbeiten", and it is
two buttons with no text: arrows move the mark, Enter takes it, starting on
"Weiter bearbeiten". The mark is **exactly one filled button among flat ones**
(`props(remove="flat")`) — a thin ring was invisible.

The button row is `position: sticky` at the bottom edge, and a dialog's error
label goes *in* that row. **Nothing in this app needs scrolling to reach an
action or to read why one was refused.** A dialog card is `max-w-3xl` so rows
hold their fields side by side.

**Read-only dialogs deliberately do not get this** (an invoice preview holds
nothing to lose, and clicking beside it is the fastest way out).

**Checked on blur, not only at save**: `app.domain.iban_validation` and
`app.domain.email_validation`. The email check reports only what is certainly
wrong (no `@`, nothing on one side, no dot in the domain, a space) and nothing
else — an empty value is valid. Both also run at save. Cross-field rules
("Firma oder Vorname/Nachname") stay at save: on blur they complain about a
form that is merely unfinished.

### One wording for an amount, one for a date

`app/formatting.py`: `format_chf` (integer Rappen → `1'234.56`), `format_date`
(German), `MISSING = "—"`. Do not hand-roll `f"{x / 100:.2f} CHF"`. The three
remaining `/ 100` are right (two feed `ui.number`, one is a numeric column).
`format_date` does **not** touch an ISO string inside `ui.input(type=date)` or
a sort key.

**Neither helper invents a value.** `None` is an em dash, not `0.00` and not
today.

Card lists say how many they show via `app/gui/list_footer.py`'s
`render_count` (a table says it itself). An empty list offers "Filter
zurücksetzen" only when `bar.is_filtering()`, and says something different
when nothing is filtered ("Noch kein Austritt gestartet."). An empty Mahnwesen
worklist gets no suggestion — it is good news.

### One search box, for every Stammdaten record

`app/domain/global_search.py` matches, `app/gui/global_search.py` is the box in
the header on every page. It searches what the lists already search, no more.
Substring, folded through `fold_for_sort`, **no fuzziness** and **no relevance
score**: groups come in data-model order (Trafokreis → Standort → Messpunkt →
LEG → Person), sorted inside a group by that group's own keys. A group caps at
six and says how many more. Keys come from `app.gui.keyboard` (a layer while
the list is open). The first hit is marked as soon as there are results;
leaving for a hit empties the box. A Trafokreis has no detail page, so its
hits lead to the Trafokreise list.

### The fix loop has to close

`render_detail_header` from `app/gui/detail_header.py` starts all four detail
pages (Person, Standort, Messpunkt, LEG): breadcrumb plus a Bearbeiten button.
The not-found branch keeps a plain link. Saving from a detail page does
`ui.navigate.reload()` — the "Gespeichert." toast is lost to it, accepted.
Dialogs live in their own modules with one shape:
`open_leg_form(existing=..., on_saved=...)`, likewise `site_form`,
`person_form`, `metering_point_form`.

`app/gui/list_state.py` keeps search text, every filter, the sort key *and*
direction, and page plus page size — per route. Pages say nothing beyond their
route: `FilterBar("/persons")` and `paged_table(route="/persons", ...)`.
Module-level store, **not `app.storage`** (one native window, one
administrator); it dies with the process, which is right. Only controls are
kept, never rows. `tests/conftest.py` empties it around every test.

### A long list is a table, and it pages

`paged_table` from `app/gui/table_list.py` builds **every** Stammdaten list —
Trafokreise, Standorte, Messpunkte, LEGs, Personen, Zuordnungen. Cards drew
2'108 interface elements for 92 persons (337 ms); a table is one element with
rows as data (65 elements, 72 ms). So **columns are free and cards are
expensive**.

- `DEFAULT_PAGE_SIZE = 50`, `PAGE_SIZE_OPTIONS = [30, 50, 100, 0]` assigned to
  `table._props["rows-per-page-options"]` **as a list** — `props()` parses a
  string and Quasar then has nothing to offer. Tests assert the type.
- **No table may scroll sideways.** `wrap-cells`, `word-break: break-word`,
  and the content area is `max-w-screen-2xl` (still bounded).
- **A cell can be marked and copied.** Quasar's `.non-selectable` rule carries
  `!important`; `paged_table` adds `leg-selectable` and `page_frame`'s
  stylesheet undoes it with the same weight. Buttons stay unselectable. The
  class and the rule live in two modules — test both.
- **The search and filters run over all records, never over the page.** The
  page filters and sorts everything and hands the result to the table.
- Paging is Quasar's own footer (unlike sorting, where half the lists had no
  header to click). `0` = alle is kept; printing is per filter, not per page.

Each list shows the columns its reader needs: Personen is Kunden-Nr., Name,
Adresse plus actions, everything else behind the eye. A column empty for all
but a handful of records is clutter — so a deactivated person is marked **in
the name cell** (`Muster, Anna · inaktiv seit …`), not in a status column.
Zuordnungen groups by Messpunkt *before* flattening, so a move reads as two
adjacent rows, and the printout is built in the same pass.

### Problems: one marker, one filter, every list

A misconfigured record shows a **warning triangle** beside the eye and the
pencil, and the list offers **"Nur fehlerhafte Einträge"**. One mechanism, in
`app/gui/problem_markers.py`.

- **The triangle carries no text** — it says "look at this one". The eye shows
  what is wrong, the pencil fixes it.
- The filter is **hidden while nothing is marked** and switches itself off when
  the last finding goes.
- `QualityWarning` carries `subject_kind` (the `SUBJECT_*` constants) and
  `subject_id`; `problems_for(connection, kind)` groups them; `ALL_CHECKS` is
  the single list both overview and lists run.
- **A list runs only the checks that could mark its own entries**, via
  `CHECK_SUBJECTS`/`checks_for`. The table is **declared, not discovered**;
  `tests/test_check_subjects.py` re-derives it from the source with `ast`.
- `render_problem_notes` spells the findings out on the detail page and in the
  edit dialog. A dialog passes `AT_THE_FIELD` so an address finding is not
  repeated at the top.
- `summary_link` must point at a list that **marks**.
- A table renders markup, not elements, so the triangle is `TABLE_MARKER_HTML`.
  Sized `1.715em` (a bare `q-icon` inherits `1em` and looks smaller than the
  icons beside it).

Five lists carry markers: Personen, Standorte, Messpunkte, LEGs, Trafokreise.
**Aufnahmen and Austritte deliberately do not** — a half-filled membership is
not a defect. A Trafokreis has no detail page, so its finding is read in the
pencil.

### Trackers for multi-step real-world processes

`app/models/person_onboarding.py` and `person_offboarding.py` share one shape:
a `STEPS` list of (attribute, label), one optional date each, idempotent
`start_for_person()`, `current_step`/`is_complete`/`is_overdue`. Their dialogs
(`app/gui/onboarding_form.py`, `offboarding_form.py`) likewise. Follow this
shape for any new manually-confirmed process.

A finished offboarding is not a finished job: the removal offer comes once, so
`check_offboarding_completed_but_active` puts the person on the dashboard and
the Austritte page keeps the card (badged "Person noch aktiv") until settled.
Deliberately not folded into `list_in_progress`, which Debitoren reads as
"Austritt läuft". Deactivating stamps `Person.deactivated_at`; `None` for
anyone deactivated before migration 50 — no date is invented.

### Genossenschaft membership: dated, like an Assignment

`app/models/cooperative_membership.py`: one row per period, `shares`,
`valid_from`/`valid_to`, `covers()`. A share count is **not** a column on
`person` — changing it closes the running row and opens a new one. Two
differences from `Assignment`: a **gap is legitimate** (leaving and
rejoining), so `find_warnings` reports only overlaps; and membership is judged
**strictly on today** (`covers(date.today())`), because mailing "die
Genossenschafter" must not reach somebody who has not joined. Zero shares is
allowed, and `check_cooperative_members_without_shares` makes sure it is not
forgotten.

Edited in `app.gui.person_form` (`CooperativeEditor` in
`app.gui.cooperative_form`); the detail page only renders
`render_cooperative_history`, no buttons — the eye shows, the pencil changes.
The date means one thing in all three actions: **the day the new state takes
effect.** A changed count closes the running period the day before; a cleared
checkbox ends it the day before the exit; a correction **on** the start day
overwrites that period; deactivating on the day membership began deletes it. A
date before the running period is refused. Corrections commit immediately, so
the editor re-reads and tells the calling page.

## Language: English code, German UI

Identifiers, file names, schema, docstrings and comments are English. Only
user-facing text is German (labels, buttons, notifications, error messages,
PDF/CSV output, and the `{vorname}`-style placeholders in stored templates).

Two things keep German *string values*: the leg-ittigen.ch form payload keys
in `app/importers/cloudflare_client.py` (an API contract we don't control) and
the placeholder keys in `app/emailing/templates.py` / `app/domain/dunning.py`.
Persisted enums are English since migration 43 (`direction`
'consumption'/'feed_in', `person_offboarding.reason`, `account_entries.kind`,
`billing_runs.status`, `email_broadcast_log.scope`); the importers still
accept BKW's German spellings (`app.importers.base.validate_direction`).

| German | Code |
|---|---|
| Trafokreis | `SubstationArea`, `substation_area` |
| Standort | `Site`, `site` |
| Messpunkt / Messpunktbezeichnung | `MeteringPoint` / `designation` |
| Messrichtung Bezug / Einspeisung | `DIRECTION_CONSUMPTION` / `DIRECTION_FEED_IN` |
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
| Textbaustein | `MessageTemplate`, `message_template` |
| LEG, BKW, Rappen, QR-Rechnung | unchanged (proper nouns) |

## Domain model

`SubstationArea` (BKW Trafokreis, physical) → `Site` (connection site with an
address) → `MeteringPoint` (one meter, one direction) → `Assignment`
(time-bounded link to a `Person`, so a mid-quarter move splits readings).
`Leg` is the billing group a metering point is assigned to — normally one per
substation area, but it can span several. `BillingRun`/`BillingRunItem` come
from `app.domain.distribution.compute_quarter_distribution` per LEG per
quarter.

A LEG carries `production_capacity_percent` (plus the date it was read):
installed production as a percentage of the participants' total
Anschlussleistung. Art. 19e Abs. 1 StromVV requires ≥5%. **Copied in by hand
and not derivable** — a site's Anschlussleistung is not in this database.
`app/domain/production_capacity.py` turns it into the usable number: with
production unchanged, total Anschlussleistung may grow by `percent / 5` before
breaking the floor. The 5% floor is law; where "getting tight" begins is
`LegSettings.production_capacity_warn_percent`. Percentages and factors go
through `format_percent`/`format_factor`.

The BKW **discount tier** (40%/20% on the Netznutzung) is deliberately not
stored and **not derivable**: BKW's criterion is the number of Netzebenen the
shared electricity crosses, confirmed per location. A `leg.discount_level`
column existed in migrations 45/46 and was removed.

In `app.domain.participant_mix`, **Producer** is the feed-in side and
**Consumer** the consumption side — per MeteringPoint direction, not per
person; somebody with both counts on both sides. `compute_participant_roles`
answers a different question (how many **people** of each kind, counted once,
for the overview tiles) and must not be confused with it. The feed-in side
*is* the Prosumer side, so the tile reads "Prosumer" for everyone who feeds
in; those without a consumption assignment are a gap, named by
`check_feed_in_without_consumption`. Deliberately one-directional — drawing
without feeding in is the normal case.

**No recommendations, only facts.** `find_upgrade_candidates`,
`leg_should_split` and `check_leg_upgrade_potential` were removed: presence is
not viability, and a ratio threshold was deliberately not built because the
decision turns on economics, on what participants agree to, and on what BKW
confirms — none of it in this database. The LEG detail page shows only the
fact: per metering point, whether its Trafokreis already has a LEG of its own
(🟢 with the name) or would need one founded (🟠), and only on a LEG spanning
several Trafokreise. Kept because it is a fact:
`check_substation_area_one_sided`.

`app.domain.statistics.leg_balance` behind `/statistics/balance` reports and
**grades nothing** — no threshold, no colour, no verdict word
(`test_the_view_grades_nothing`). The **ordering** stands in for the verdict:
feed-in meters per consumption meter, descending, so both extremes are where
the eye lands. A LEG with no consumption meters leads; an empty LEG is pushed
past every populated one but named. Meter counts are a proxy, so the real
figure sits beside them: `shared_kwh / feed_in_kwh`, formed per 15-minute
interval **and per LEG** before summing. Without an import it reads "—", not
"0 %". Meter counts come from `distribution_by_leg`, so the Verteilung and
Ausgewogenheit views cannot disagree about one LEG's size.

### One customer, one document — the vZEV model

A participant is **one** customer: one netted amount, one reference number,
one payment, one receivables account, one dunning notice — however many sites,
metering points **and people** they are. A couple is one `Person` with two
names (`second_first_name`/`second_last_name`, migration 50). Exactly two
deliberately; a third would need a sub-table.

Both partners are contract parties: both on the address block (one line each,
salutation in front of the name), both greeted by `letter_salutation`, both
addresses in the one message (`Person.contact_emails`). The greeting is
**"Guten Tag …"** because German adjective inflection has to agree with
gender, and every earlier mechanism got that wrong. **An empty salutation is a
valid state.** Use `{briefanrede}`; `{anrede}`/`{nachname}` remain only
because they are in saved texts.

The QR-bill limits the payer name to 70 characters and `qrbill` raises for a
longer one, so a couple's overrunning name falls back to the first person
(`app.pdf.qr_bill_render.qr_debtor_name`) and says so in
`ExportResult.errors`; the address block still names both.

So there is **no per-customer billing logic**: no own distribution key, no
split into several documents, no setting that makes one participant's bill
work differently.

What such a participant gets is detail: `app/pdf/bill_breakdown.py` groups the
quarter by site and metering point and prints each site's address, its Bezug
and Einspeisung per metering point, and that site's balance. Presentation
only — the amount still comes from `app/domain/billing.py`.

**Every metering point the person held is on the document, including one that
shared nothing — then at 0.000 kWh.** A bill's line-up must not change from
quarter to quarter. Which side a meter is listed under follows its
`direction`, never which total happens to be non-zero. Everyone with an
assignment gets a document, even at 0.00 CHF
(`app.domain.distribution._seed_participants`). The one figure withheld from a
zero document is the flat paper-invoice fee.

Consequences in `app/pdf/layout.py`: table drawing breaks across pages, and a
label that would collide with the kWh column is truncated — reportlab
complains about neither.

### Exactly one current set of documents

Re-billing a quarter deletes the run and creates a new one with fresh item
ids, so `app.pdf.export_service` removes the superseded documents. Three
details: only files matching `_GENERATED_PATTERNS` are touched (the folder may
hold the administrator's own notes), removal happens **after** the new
documents are written, and what was removed is reported in
`ExportResult.removed_paths`.

`create_billing_runs_for_all_legs` bills and exports every LEG in one pass. A
failing LEG lands its message in its own `LegRunOutcome` and the rest
continue; `LegNotAssignedError` stays the one deployment-wide abort.

### Before billing, say what the quarter contains

`app.domain.statistics.quarter_energy_totals` answers which quarter is worth
billing and whether the last import landed. A quarter can hold a hundred
thousand readings and be unbillable (no feed-in at all → nothing to share →
every document 0.00); `QuarterEnergy.note` says so **before** the run.
`missing_metering_points` compares metering points that held an assignment
against those that reported, using the distribution's own overlap rule.

### Billing is a guided run, and the gate is not decorative

`app/models/billing_cycle.py` tracks a quarter like `person_onboarding` tracks
a membership: a fixed `STEPS` list, one optional date each, one row per
quarter covering every LEG. Rendered by `app/gui/billing_cycle_view.py`.

`app/domain/billing_checks.py` **blocks** rather than warns, because a
metering point whose readings were never imported does not merely go unbilled
— its absence enlarges everyone else's share, and the invoices look normal.
Four blocking checks: every metering point has a LEG; every assigned metering
point delivered a full quarter of 15-minute values (zero kWh is a statement,
no data is not); assignments without overlaps or gaps; locally delivered
equals locally drawn per LEG. The last is an engine invariant, so a difference
only ever means unattributable energy (`unassigned_kwh`), never production
exceeding consumption — surplus is settled with BKW and never reaches this
app.

- **The balance tolerance scales with the participant count** (each person's
  totals round independently), like `verify_sum_balance` does with Rappen.
- **Control points are recomputed on every page load and never stored.** When
  they are red although step 2 carries a date, the page says the data changed.

The gate is stepped past only via `record_override`, which refuses a blank
reason and keeps the text visible. The control points stay red: an override
excuses proceeding, it does not repair anything.

### Receivables

`app/pdf/qr_reference.py`'s `generate_qrr_reference`/`parse_qrr_reference` are
pure functions encoding `(customer_number, billing_run_id, item_id)` — nothing
about a sent invoice's reference is stored; a bank match is decoded straight
back to the `BillingRunItem`. `app/domain/bank_reconciliation.py` matches
camt.053/054 transactions to a person (auto via decoded reference, or a ranked
suggestion via IBAN / customer number in text / name similarity) and books an
`AccountEntry`. `app/domain/dunning.py` implements the LEG's own **2-stage**
Reglement (not the generic 3-stage/fee model), gated on the person's **overall
balance**, not one item's state.

### Textbausteine and the Beitrittserklärung

`message_template` (migration 52/53) holds every predefined mail: `occasion`,
`step`, `trigger_kind` (`TRIGGER_STEP_DONE` / `TRIGGER_STEP_PENDING`),
`deadline_days`, `auto_attachments`. **Mails go out only on a manual click** —
no schedule, no "send all", ever. `person_message_log` is the UI's source of
truth for "button or date".

`app/domain/auto_attachments.py` is the registry of tickable attachments
(`KEY_MEMBERSHIP_CONTRACT`, `KEY_INVOICE`), so the list can grow without the
dialog changing shape. The contract is **generated, not attached**:
`app/domain/membership_contract.py` gathers the fields,
`app/pdf/membership_contract.py` draws page 1 and appends pages 2+ of the
stored original from `leg_document`. Ort, Datum and the signature stay blank.
The Trafokreis comes from `bkw_designation`, not `name`.

## Tooling and CI

`.github/workflows/ci.yml` runs on every push/PR: `ruff check`, pytest,
`bandit -ll`, `pip-audit`. Formatting is `ruff format` (line length 110).

The `heavy` marker is **added automatically** from the fixtures a test asks
for (`demo_data`, `real_demo_data`), in `pytest_collection_modifyitems`.

`tests/conftest.py` builds the demo database once per session into a template
and restores it with SQLite's `backup()`; `real_demo_data` marks the few tests
wanting the genuine article. The template is built **on first use**, as a
module-level cache. Two traps: the builder must hold the **real**
`create_demo_data` captured at import (`_REAL_CREATE_DEMO_DATA`), or lazy
building calls the autouse restore, which calls the builder (first symptom:
`'WindowsPath' object has no attribute '_str'`, from pathlib running out of
stack); and it is a module-level cache rather than a fixture because
`request.getfixturevalue` from inside another fixture trips pytest's
`assert not self._finalizers`.

**Treat a single timing as noise** — four full runs on this machine came out
2:03, 2:16, 2:28 and 2:45.

Tests must stay **order- and process-independent** (xdist hands each worker an
arbitrary slice). A globally registered name — a probe route
`ui.page("/probe-...")` above all — must be unique across the whole suite.

**A page rendering is not evidence that it works.** The `nicegui 3.15 → 3.16`
bump changed uploads from `event.name`/`event.content.read()` to
`event.file.name`/`await event.file.read()`, and for nine days every broadcast
went out without its attachment. Nobody saw it because NiceGUI runs a handler
inside `with parent_slot:` but calls `handle_exception` outside it, so for a
**synchronous** handler `ui.notify` raises `RuntimeError` and
`app/gui/safe_notify.py` swallowed it. `_handle_ui_exception` now enters a
connected client's context before notifying, and `safe_notify` logs its
give-up branch at ERROR.

So when bumping a GUI dependency or touching an event handler, **drive at
least one real interaction per changed surface**. Where a handler reads
framework objects, extract that part into a module-level function a test can
call (`app/gui/upload.py`'s `read_uploaded_file`) and pin the event shape in a
contract test (`tests/test_email_attachments.py`).

A page function's own tests do not touch routing: `/backup` answered HTTP 422
for a session because a helper had been inserted between `@ui.page(...)` and
its function, making the helper the route.
`tests/test_page_routes.py` checks the routing table itself.

`tests/conftest.py` has an autouse fixture pointing `connection_scope()` at a
throwaway database — without it any test rendering a page opens the real
`data/leg_abrechnung.sqlite3`.

## The official address register

`app/importers/address_register.py` downloads swisstopo's **Amtliches
Verzeichnis der Gebäudeadressen** on one click into
`data/adressregister.sqlite3`; `app/domain/address_lookup.py` narrows it while
typing and checks a finished address. Four places use it: `site_form.py`,
`person_form.py`, `pages/settings.py` (the LEG's own sender address — the
creditor on every QR-bill) and `pages/web_registrations.py`.

- **Swiss Post's data was rejected, not overlooked**: its licence forbids
  passing the data on. swisstopo's is free for commercial use and
  redistribution; naming the source is the only condition.
- **Downloaded whole rather than queried** — geo.admin.ch's API would send
  fragments of a member's address to a federal server on every keystroke.
- **A file of its own**, never tables in the member database.
  `PRAGMA synchronous = OFF` is defensible there and must not spread.

Four decisions, each the opposite of the obvious one:

- **Do not filter `ADR_OFFICIAL = true`** — 86 of 92 live sites validate
  against the whole register, only 83 against the official rows. `official`
  and `status` only influence the *order* of suggestions.
- **Compare the house number as folded text**, never split into figure and
  letter the way `address_key` does (331'401 official addresses are dotted).
- **The locality is always the postal one** (`ZIP_LABEL`), never `COM_NAME`:
  postal code 3048 lies in two municipalities. A stored municipality is
  accepted rather than called wrong, and `COM_NAME` is never filled in.
- **`Site.municipality` therefore holds a postal locality despite its name.**
  Do not "fix" the data to match the identifier.

**A street that exists elsewhere is a wrong postal code, not a wrong street.**
Almost every Swiss street name ends in "strasse", so the shared suffix carries
the similarity score and a threshold cannot separate the cases (a genuinely
bad suggestion scored 0.733, a real postal-code error 0.737). So `verify`
first asks whether the street exists under another postal code and reports
`FIELD_POSTAL_CODE` if it does, and then skips the locality check. Streets use
a stricter cutoff (`_CUTOFF_STREET`) than localities.

**Which field a "Ja" writes is an explicit mapping** in
`SuggestionBox.accept`, never "everything that is not the locality is the
street". That shape cost two defects — a postal-code finding written into the
street field, and a house-number correction overwriting the street with "4". A
field the mapping does not know is **left alone**
(`test_yes_writes_only_the_field_the_finding_is_about`).

- **One hint, one wording, yes or no**: "Meinten Sie: Worblaufen?" with Ja and
  Nein, nothing else. Where nothing is close enough: "Nicht im amtlichen
  Verzeichnis." with only a Nein.
- **The question is asked at the field**, directly under the row holding the
  value it would replace (`app/gui/address_input.py`). Never in a card above
  the list — a name and a suggestion with no sight of the field is unanswerable.
- `summarise_warnings` collapses several findings of one kind into one counted
  line, grouped by category and `summary`, never by `link`. A **single**
  finding keeps its own message. A check with no `summary` is never collapsed.
- **`verify` says nothing until there is an address to check** — a blank street
  or postal code returns nothing, and a blank house number is skipped.
- **No debounce on the address fields** (the dialog's own 300 ms plus the
  suggestion box's 250 ms left the list empty until another key was pressed).
- **Nein stores the confirmed value**, not a flag or a date (migration 51), so
  the dismissal expires by itself when the address changes. Two columns per
  record, because the street and locality findings are independent, and
  `update()` must not write them.
- Persons are checked only when `billing_country` is CH, but then fully.
- A "Nein" in a dialog is collected in `SuggestionBox.dismissals` and written
  by `store_dismissals` **after** the record is saved (a new record has no id).
- **The cursor starts in the field meant to be typed into** — Adresse on a
  Standort, Firma on a Person, Messpunktnummer on a Messpunkt.
- The suggestion list floats as a `ui.menu` anchored to the field; sitting in
  the layout it resized the dialog on every keystroke. A postal code already in
  the form **ranks** the suggestions — ranked, not filtered.
- **Down/up walk the entries (wrapping), Enter takes the reached one, and Enter
  takes nothing while nothing is highlighted** — a street suggestion can be a
  correctly spelled *different* street. Keys are bound on the field, not the
  menu.

**The update must not block.** The download streams with `httpx.AsyncClient`
and `build_register` is a **generator** yielding every 5'000 rows. Progress
lives in a module-level object in `app/gui/address_register_task.py` (not on a
client) and `page_frame` shows it on every route. But yielding is not enough:
`CREATE INDEX` over 3.3 million rows blocked 2'070 ms and filled the log with
`TimeoutError: JavaScript did not respond within 1.0 s`. So `build_register`
stops before indexing and `finalise_register` does the indexing and the swap
under `asyncio.to_thread` (SQLite releases the GIL). Measured after: longest
block 179 ms.

76 s for the whole click (16 s for 143 MB, then 3'303'418 rows), 148 MB on
disk. The download URL is read from the STAC catalogue, then checked against
`_ALLOWED_HOSTS` over HTTPS — the one path in an offline app that fetches and
writes. Staleness (`STALE_AFTER_DAYS`) is shown as the **data** date from
STAC, not the download time.

**Without a register nothing breaks**: no suggestions, no findings, and the
Adressregister page is the only place that says so. `tests/conftest.py` points
the register path at a file that does not exist — which works only because the
path is resolved in the function body, not as a default argument.

## The repository is public

`schopf16/leg-abrechnung` is **public** (for free CodeQL, secret scanning,
Dependabot, Actions). Whatever is committed is world-readable, immediately and
permanently — a later `git rm` does not remove it from history.

- **Nothing that identifies a member, ever**: no name, address, IBAN, email or
  customer number — not in code, not in a fixture, not in a commit message,
  not in a PR description. Test data is invented (`example.invalid`,
  `Muster`/`Beispiel`). Real figures stay aggregate ("26 von 29 Messpunkten").
- **Never `git add -A`.** Name every path explicitly. The administrator keeps
  their own files and temporary checks in the project folder; commit only what
  is needed to develop, run or test.
- `.gitignore` covers `data/`, `backups/`, `logs/`, `output/`, `*.sqlite3`,
  `config.local.*`, `.env`, `*.pdf`, `*.xlsx`, `*.docx`.
- Licensed **GPL-3.0-or-later**. GPL-2 was ruled out because `svglib` is
  LGPL-3.0; every other dependency is MIT or BSD. Check a new dependency's
  licence before pinning it.
- A PR only on the administrator's explicit word; committing and pushing to a
  branch is autonomous.

## Security review is Claude's job, not GitHub's

GitHub runs CodeQL (default setup), secret scanning with push protection, and
Dependabot. **AI Scan and Copilot Autofix are deliberately off and must stay
off** — both need a paid Copilot licence this account does not have, and left
on they fail red with `You are not licensed to use Copilot` on every PR. If
that red check reappears it is a licence error, not a finding.

Nothing stops being *detected* by that (CodeQL handles Python and Actions);
what is lost is the suggested fix and a second pair of eyes.

**So on every review and before every push, review the change for security
implications yourself.** Use the `security-review` skill on the branch diff.
Pay attention to what bandit and CodeQL cover badly and this app actually
handles: personal data of real members, the Microsoft Graph credentials and
the leg-ittigen.ch API token (`app/config.py`), SQL built by string
formatting, anything written to `logs/` (log lines can contain real names and
amounts), file paths from user input, and the QR-bill/Rappen arithmetic, where
a wrong number is a wrong invoice to a real person. When CodeQL does flag
something, propose the fix in the PR.
