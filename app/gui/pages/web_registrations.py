"""Web-Registrierungen page: inbox for registrations submitted through the
public form on leg-ittigen.ch (see `app.importers.registration_sync`).

Person, site and every reported MeteringPoint can each be taken over
separately -- "... übernehmen" opens the matching create dialog (see
`app.gui.person_form`/`site_form`/`metering_point_form`) prefilled from the
registration, so nothing has to be retyped, and records that this item
was taken over once actually saved. Deliberately three independent
actions rather than one "accept everything" button: matching a reported
meter (and its site) against a *new* record still needs a human
judgment call (which LEG, which direction, is this really the same
site as an existing site), so each piece is confirmed on its own.
Assignment (linking a taken-over Person to a taken-over MeteringPoint) stays a
manual step in `/assignments`, as it always was.

Each of the three can be closed three ways, and that is the point: the
record is created from the registration, an already-existing record is
*linked*, or the administrator marks the item by hand. Linking matters
more than it sounds -- everyone living in one apartment block shares a
single site, so from the second registration at that address onwards
there is nothing to create, and before this existed such an entry could
never reach `is_fully_processed` and sat in the inbox for good. Where a
match is found (`_registration_status`), the card therefore offers only
"Vorhandenen ... verknüpfen", never a second create button that would
duplicate the site.

Matching is exact on email / address / Messpunktbezeichnung, apart from
case and padding. No fuzzy matching: linking a registration to the wrong
address is worse than not finding it, and the case it would serve -- a
typo in the submitted address -- is covered by hand-marking instead,
which is offered precisely when nothing matched.

There is no separate "reviewed" flag: an entry with nothing left to take
over (`WebRegistration.is_fully_processed`) simply has nothing more to do
here, and can be deleted once truly obsolete.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

from nicegui import ui

from app.config import ConfigError, get_leg_api_token
from app.db.connection import connection_scope
from app.gui.metering_point_form import open_metering_point_form
from app.gui.navigation import page_frame
from app.gui.person_form import open_person_form
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.gui.site_form import open_site_form
from app.gui.sorting import (
    SortOption,
    address_key,
    apply_sort,
    person_name_key,
    render_sort_select,
    sort_description,
)
from app.importers.cloudflare_client import (
    CloudflareApiError,
    CloudflareAuthError,
    delete_submissions,
)
from app.importers.registration_sync import sync_registrations
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import site as site_repo
from app.models import web_registration as web_registration_repo
from app.models.metering_point import MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.web_registration import WebRegistration, WebRegistrationMeter

#: `(label, field)` pairs for the printed table.
PRINT_COLUMNS = [
    ("Name", "name"),
    ("Eingegangen", "submitted"),
    ("E-Mail", "email"),
    ("Telefon", "phone"),
    ("Adresse", "address"),
    ("BKW-Kundennummer", "bkw_customer_number"),
    ("Zähler", "meters"),
    ("Status", "status"),
]


def _print_row(reg: WebRegistration) -> dict:
    """Convert a `WebRegistration` into a row dict for the printed table.

    Args:
        reg: Registration to convert.

    Returns:
        A dict with the fields required by `PRINT_COLUMNS`.
    """
    return {
        "name": reg.display_name,
        "submitted": reg.submitted_at,
        "email": reg.email,
        "phone": reg.phone,
        "address": f"{reg.street} {reg.house_number}, {reg.postal_code} {reg.city}".strip(", "),
        "bkw_customer_number": reg.bkw_customer_number,
        "meters": ", ".join(m.meter_number for m in reg.meters) or "-",
        "status": "Vollständig übernommen" if reg.is_fully_processed else "Offen",
    }


def _site_key(street: str, house_number: str, postal_code: str) -> tuple[str, str, str]:
    """Normalise an address into the key both sides of the match use.

    Args:
        street: Street name.
        house_number: House number.
        postal_code: Postal code.

    Returns:
        The three parts, trimmed and lowercased. Deliberately exact apart
        from case and padding: a near-match is a judgment call, and
        guessing one wrong would silently link a registration to the
        wrong address. A typo is handled by the administrator instead,
        via "Von Hand als übernommen markieren".
    """
    return (street.strip().lower(), house_number.strip().lower(), postal_code.strip().lower())


@dataclass
class _RegistrationStatus:
    """The already-existing records one registration's Person/site/meters
    match, if any, *without* having been taken over via this page.

    Holds the matched records themselves rather than plain booleans, so
    the confirmation dialog can name what it is about to link -- linking
    the wrong address is not something to confirm blind.

    Attributes:
        person: The existing Person with this registration's email, or `None`.
        site: The existing site at this registration's address, or `None`.
        metering_points: `{meter.id: MeteringPoint or None}` per reported meter.
    """

    person: Optional[Person]
    site: Optional[Site]
    metering_points: dict[int, Optional[MeteringPoint]]


def _registration_status(
    reg: WebRegistration,
    persons_by_email: dict[str, Person],
    sites_by_address: dict[tuple[str, str, str], Site],
    metering_points_by_designation: dict[str, MeteringPoint],
) -> _RegistrationStatus:
    """Compute which existing records this registration already matches.

    Args:
        reg: Registration to check.
        persons_by_email: Existing Persons keyed by non-empty `contact_email`.
        sites_by_address: Existing sites keyed by `_site_key`.
        metering_points_by_designation: Existing MeteringPoints by `designation`.

    Returns:
        The computed `_RegistrationStatus`.
    """
    return _RegistrationStatus(
        person=persons_by_email.get(reg.email) if reg.email else None,
        site=sites_by_address.get(_site_key(reg.street, reg.house_number, reg.postal_code)),
        metering_points={m.id: metering_points_by_designation.get(m.meter_number) for m in reg.meters},
    )


def _parse_bkw_customer_number(value: str) -> Optional[int]:
    """Try to interpret a registration's free-text BKW-customer number as an
    integer, for prefilling `Person.bkw_customer_number` (which is validated).

    Args:
        value: Free-text value as submitted through the web form.

    Returns:
        The parsed integer, or `None` if `value` is empty or not purely numeric.
    """
    stripped = value.strip()
    return int(stripped) if stripped.isdigit() else None


def _parse_submitted_date(value: str) -> date:
    """Parse a registration's `submitted_at` timestamp into a calendar date.

    The leg-ittigen.ch API's timestamp format is not guaranteed to be
    strict ISO-8601 (real data observed as `"2026-08-09 19:11:04"`) --
    parsed defensively, falling back to today if it cannot be interpreted,
    since this only seeds the onboarding tracker's step-1 date, which
    remains editable afterwards regardless.

    It is also the key behind this page's default order ("Eingang, neuste
    zuerst", see `SORT_OPTIONS`), where the fallback is less harmless: a
    registration whose timestamp the API delivered in an unexpected shape
    is dated today and therefore jumps to the very top of the inbox. That
    is the deliberate trade-off -- a malformed entry being too visible
    beats it sinking to the bottom unnoticed -- but it is the reason a
    registration can appear "newer" than it is.

    Args:
        value: Raw `submitted_at` value from the registration.

    Returns:
        The parsed date, or today's date if `value` could not be parsed.
    """
    try:
        return datetime.fromisoformat(value.replace(" ", "T")).date()
    except ValueError:
        return date.today()


#: Orders the Web-Registrierungen inbox offers, default first. Newest
#: first here, unlike every other list: this is an inbox, and what arrived
#: last is what has not been looked at yet.
SORT_OPTIONS = [
    SortOption(
        "submitted_at",
        "Eingang (neuste zuerst)",
        lambda r: _parse_submitted_date(r.submitted_at),
        reverse=True,
    ),
    # A registration has the same name fields as a Person, so it keys the
    # same way and the same people come out in the same order as elsewhere.
    SortOption("last_name", "Nachname", person_name_key),
    SortOption(
        "address",
        "Adresse",
        lambda r: address_key(r.street, r.house_number, r.postal_code, r.city),
    ),
    SortOption(
        "processed",
        "Offene zuerst",
        # Negated so ties break newest-first, putting the open ones at the
        # top in the same order as under "Eingang", without reversing the
        # processed/open grouping itself.
        lambda r: (r.is_fully_processed, -_parse_submitted_date(r.submitted_at).toordinal()),
    ),
]


@ui.page("/web-registrations")
def web_registrations_page() -> None:
    """Render the Web-Registrierungen inbox page.

    Returns:
        None.
    """
    with page_frame("/web-registrations", "Web-Registrierungen"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Registrierungen, die Interessierte über das Anmeldeformular "
                "auf leg-ittigen.ch eingereicht haben -- pro Person/Firma ein "
                "Eintrag, mit allen dabei gemeldeten Zählern. Person, "
                "Standort und jeder gemeldete Zähler (als Messpunkt) können "
                "hier je einzeln übernommen werden; die Zuordnung "
                "zwischen ihnen bleibt ein manueller Schritt unter "
                "„Zuordnungen“."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    heading="Web-Registrierungen",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: [_print_row(r) for r in visible_regs],
                    get_filter_description=lambda: (
                        "inkl. vollständig übernommene" if show_complete_switch.value else None
                    ),
                    # Named on its own line: a printout is read away from
                    # the screen, where the order is not self-evident.
                    get_sort_description=lambda: sort_description(SORT_OPTIONS, sort_select),
                )
                ui.button("Registrierungen abrufen", on_click=lambda: do_sync())

        with ui.row().classes("w-full items-center gap-4"):
            show_complete_switch = ui.switch("Auch vollständig übernommene anzeigen")
            sort_select = render_sort_select(SORT_OPTIONS, lambda: refresh())

        list_container = ui.column().classes("w-full gap-2 mt-2")

        visible_regs: list[WebRegistration] = []

        def _confirm(title: str, body: str, confirm_label: str, on_confirm: Callable[[], None]) -> None:
            """Ask before writing a take-over flag that creates nothing.

            Linking or hand-marking silently closes an inbox item, so it
            is worth one look -- above all at *which* record is about to
            be linked.

            Args:
                title: Dialog heading.
                body: Explanation of what the flag does and does not do.
                confirm_label: Label of the confirming button.
                on_confirm: Called once confirmed, dialog already closed.

            Returns:
                None.
            """
            with ui.dialog() as confirm, ui.card().classes("w-full max-w-md"):
                ui.label(title).classes("font-bold")
                ui.label(body).classes("text-caption text-grey-7")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do() -> None:
                        confirm.close()
                        on_confirm()

                    ui.button(confirm_label, on_click=do)
            confirm.open()

        def _take_over_row(
            what: str,
            *,
            done: bool,
            existing: Optional[str],
            on_create: Callable[[], None],
            on_take_over: Callable[[], None],
        ) -> None:
            """Render one item's take-over control in whichever of its three
            states applies.

            Nothing left to do -- just the badge. A matching record already
            exists -- offer to link it, *not* to create a second one: two
            members of the same apartment block share one site, and
            creating a duplicate is the mistake this row exists to
            prevent. Nothing matches -- offer the prefilled create dialog,
            plus a hand-marking escape hatch, because a typo in the
            submitted address must not leave the entry stuck in the inbox
            forever.

            Args:
                what: The item's German name, e.g. "Standort".
                done: Whether this item needs no further action.
                existing: Display text of the already-existing record this
                    registration matches, or `None` if nothing matches.
                on_create: Opens the prefilled create dialog.
                on_take_over: Marks the item as taken over, creating nothing.

            Returns:
                None.
            """
            with ui.row().classes("items-center gap-2"):
                if done:
                    ui.badge("übernommen", color="positive")
                    return
                if existing is not None:
                    ui.button(
                        f"Vorhandenen {what} verknüpfen",
                        on_click=lambda: _confirm(
                            f"{what} verknüpfen?",
                            f"Es wird nichts neu erstellt. Die Registrierung wird mit dem "
                            f"bereits erfassten Eintrag „{existing}“ als erledigt markiert.",
                            "Verknüpfen",
                            on_take_over,
                        ),
                    ).props("dense flat color=primary size=sm")
                    ui.badge("existiert bereits", color="info")
                    return
                ui.button(f"{what} übernehmen", on_click=on_create).props("dense flat color=primary size=sm")
                ui.button(
                    icon="done_all",
                    on_click=lambda: _confirm(
                        f"{what} von Hand als übernommen markieren?",
                        f"Nur wählen, wenn dieser {what} bereits erfasst ist, aber wegen "
                        f"einer abweichenden Schreibweise nicht automatisch gefunden wurde. "
                        f"Es wird nichts erstellt und nichts verknüpft -- der Punkt gilt "
                        f"danach einfach als erledigt.",
                        "Als übernommen markieren",
                        on_take_over,
                    ),
                ).props("dense flat color=grey size=sm").tooltip(
                    f"{what} ist schon erfasst, wurde aber nicht gefunden (z. B. Tippfehler) "
                    f"-- von Hand als übernommen markieren"
                )

        def render_card(reg: WebRegistration, status: _RegistrationStatus) -> None:
            """Render one registration as a card with wrapping field groups.

            Args:
                reg: Registration to render.
                status: Precomputed "does a match already exist?" status
                    (see `_registration_status`).

            Returns:
                None.
            """
            with ui.card().classes("w-full" + ("" if not reg.is_fully_processed else " opacity-60")):
                with ui.row().classes("w-full items-start gap-6 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[200px]"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(reg.display_name or "-").classes("font-bold")
                            if reg.is_fully_processed:
                                ui.badge("Vollständig übernommen", color="grey")
                        ui.label(f"Eingegangen: {reg.submitted_at}").classes("text-caption text-grey-6")
                    with ui.column().classes("gap-0 min-w-[180px]"):
                        ui.label(reg.email or "-")
                        ui.label(reg.phone or "-").classes("text-grey-7")
                    with ui.column().classes("gap-0 min-w-[200px]"):
                        ui.label(f"{reg.street} {reg.house_number}".strip() or "-")
                        ui.label(f"{reg.postal_code} {reg.city}".strip() or "-")
                    with ui.column().classes("gap-0 min-w-[180px]"):
                        ui.label(f"BKW-Kundennummer: {reg.bkw_customer_number or '-'}")
                        ui.label(f"IBAN: {reg.iban or '-'}").classes("text-grey-7")
                    with ui.column().classes("gap-0 min-w-[200px]"):
                        ui.label(reg.message or "-").classes("text-grey-7")
                    with ui.row().classes("gap-1"):
                        ui.button("Löschen", on_click=lambda r=reg: on_delete(r)).props(
                            "dense flat color=negative"
                        )

                ui.separator().classes("my-2")
                ui.label("Übernahme").classes("text-caption text-grey-6")
                with ui.column().classes("gap-1"):
                    _take_over_row(
                        "Person",
                        done=reg.person_taken_over,
                        existing=status.person.display_name if status.person else None,
                        on_create=lambda r=reg: on_take_over_person(r),
                        on_take_over=lambda r=reg: mark_person_done(r),
                    )
                    _take_over_row(
                        "Standort",
                        done=reg.site_taken_over,
                        existing=status.site.full_address if status.site else None,
                        on_create=lambda r=reg: on_take_over_site(r),
                        on_take_over=lambda r=reg: mark_site_done(r),
                    )
                    if reg.meters:
                        for meter in reg.meters:
                            with ui.row().classes("items-center gap-2"):
                                meter_label = meter.meter_number + (f" ({meter.note})" if meter.note else "")
                                ui.label(meter_label).classes("font-mono text-caption min-w-[160px]")
                                existing_mp = status.metering_points.get(meter.id)
                                _take_over_row(
                                    "Messpunkt",
                                    done=meter.metering_point_taken_over,
                                    existing=existing_mp.designation if existing_mp else None,
                                    on_create=lambda r=reg, m=meter: on_take_over_metering_point(r, m),
                                    on_take_over=lambda m=meter: mark_metering_point_done(m),
                                )
                    else:
                        ui.label("Keine Zähler gemeldet.").classes("text-caption text-grey-6")

        def refresh() -> None:
            """Reload the registrations list according to the current filter.

            Returns:
                None.
            """
            nonlocal visible_regs
            with connection_scope() as connection:
                all_regs = web_registration_repo.list_all(connection)
                persons_by_email = {
                    p.contact_email: p for p in person_repo.list_all(connection) if p.contact_email
                }
                sites_by_address = {
                    _site_key(s.street, s.house_number, s.postal_code): s
                    for s in site_repo.list_all(connection)
                }
                metering_points_by_designation = {
                    mp.designation: mp for mp in metering_point_repo.list_all(connection)
                }
            regs = (
                all_regs if show_complete_switch.value else [r for r in all_regs if not r.is_fully_processed]
            )
            visible_regs = apply_sort(regs, SORT_OPTIONS, sort_select)
            list_container.clear()
            with list_container:
                if not visible_regs:
                    ui.label(
                        "Keine Registrierungen."
                        if show_complete_switch.value
                        else "Keine offenen Registrierungen."
                    )
                for reg in visible_regs:
                    render_card(
                        reg,
                        _registration_status(
                            reg, persons_by_email, sites_by_address, metering_points_by_designation
                        ),
                    )

        show_complete_switch.on_value_change(lambda _: refresh())

        def on_take_over_person(reg: WebRegistration) -> None:
            """Card button handler: open a prefilled Person-creation dialog.

            Also starts the person's onboarding tracker (see
            `app.models.person_onboarding`), dated from the registration.

            Args:
                reg: Registration to take over.

            Returns:
                None.
            """
            prefill = {
                "company": reg.company,
                "salutation": reg.salutation,
                "first_name": reg.first_name,
                "last_name": reg.last_name,
                "street": reg.street,
                "house_number": reg.house_number,
                "postal_code": reg.postal_code,
                "city": reg.city,
                "email": reg.email,
                "phone": reg.phone,
                "iban": reg.iban,
            }
            bkw_customer_number = _parse_bkw_customer_number(reg.bkw_customer_number)
            if bkw_customer_number is not None:
                prefill["bkw_customer_number"] = bkw_customer_number

            def on_person_saved(saved_person) -> None:
                with connection_scope() as connection:
                    web_registration_repo.mark_person_taken_over(connection, reg.id)
                    person_onboarding_repo.start_for_person(
                        connection,
                        saved_person.id,
                        registered_at=_parse_submitted_date(reg.submitted_at),
                    )
                refresh()

            open_person_form(prefill=prefill, on_saved=on_person_saved)

        def mark_person_done(reg: WebRegistration) -> None:
            """Close this registration's Person item without creating one.

            Args:
                reg: Registration whose Person is already covered.

            Returns:
                None.
            """
            with connection_scope() as connection:
                web_registration_repo.mark_person_taken_over(connection, reg.id)
            safe_notify("Person als übernommen markiert.", type="positive")
            refresh()

        def mark_site_done(reg: WebRegistration) -> None:
            """Close this registration's site item without creating one.

            Args:
                reg: Registration whose site is already covered.

            Returns:
                None.
            """
            with connection_scope() as connection:
                web_registration_repo.mark_site_taken_over(connection, reg.id)
            safe_notify("Standort als übernommen markiert.", type="positive")
            refresh()

        def mark_metering_point_done(meter: WebRegistrationMeter) -> None:
            """Close one reported meter without creating a MeteringPoint.

            Args:
                meter: The reported meter that is already covered.

            Returns:
                None.
            """
            with connection_scope() as connection:
                web_registration_repo.mark_metering_point_taken_over(connection, meter.id)
            safe_notify("Messpunkt als übernommen markiert.", type="positive")
            refresh()

        def on_take_over_site(reg: WebRegistration) -> None:
            """Card button handler: open a prefilled site-creation dialog.

            Args:
                reg: Registration to take over.

            Returns:
                None.
            """
            prefill = {
                "street": reg.street,
                "house_number": reg.house_number,
                "postal_code": reg.postal_code,
                "municipality": reg.city,
            }

            def on_site_saved(_site) -> None:
                with connection_scope() as connection:
                    web_registration_repo.mark_site_taken_over(connection, reg.id)
                refresh()

            open_site_form(prefill=prefill, on_saved=on_site_saved)

        def on_take_over_metering_point(reg: WebRegistration, meter: WebRegistrationMeter) -> None:
            """Card button handler: open a prefilled MeteringPoint-creation dialog.

            Pre-selects the site matching this registration's address
            if one already exists (typically because it was just taken
            over above) -- otherwise leaves it for the administrator to pick.

            Args:
                reg: Registration the meter was reported with.
                meter: The specific reported meter to take over.

            Returns:
                None.
            """
            with connection_scope() as connection:
                matching_site = site_repo.find_by_address(
                    connection, reg.street, reg.house_number, reg.postal_code
                )
            prefill = {"metering_point_number": meter.meter_number}
            if matching_site is not None:
                prefill["site_id"] = matching_site.id

            def on_metering_point_saved(_metering_point) -> None:
                with connection_scope() as connection:
                    web_registration_repo.mark_metering_point_taken_over(connection, meter.id)
                refresh()

            open_metering_point_form(prefill=prefill, on_saved=on_metering_point_saved)

        def on_delete(reg: WebRegistration) -> None:
            """Card button handler: delete a registration after confirmation,
            both locally and from the remote leg-ittigen.ch Worker database.

            Args:
                reg: Registration to delete.

            Returns:
                None.
            """
            with ui.dialog() as confirm, ui.card():
                ui.label(f'"{reg.display_name or reg.email}" wirklich löschen?').classes("font-bold")
                if not reg.is_fully_processed:
                    ui.label(
                        "⚠ Person, Standort und/oder Messpunkt(e) dieser "
                        "Registrierung wurden noch nicht vollständig "
                        "übernommen. Beim Löschen werden die Daten "
                        "unwiderruflich aus der Web-Datenbank entfernt -- "
                        "danach gibt es keine Kopie mehr."
                    ).classes("text-negative text-body2 font-bold")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        try:
                            token = get_leg_api_token()
                        except ConfigError as exc:
                            confirm.close()
                            safe_notify(str(exc), type="negative", timeout=8000)
                            return
                        try:
                            delete_submissions([reg.cloudflare_id], token)
                        except (CloudflareAuthError, CloudflareApiError) as exc:
                            confirm.close()
                            safe_notify(str(exc), type="negative", timeout=8000)
                            return
                        with connection_scope() as connection:
                            web_registration_repo.delete(connection, reg.id)
                        confirm.close()
                        # notify before refresh() -- see app.gui.safe_notify for why
                        safe_notify("Gelöscht.", type="warning")
                        refresh()

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        def do_sync() -> None:
            """Top button handler: fetch and apply new registrations.

            Returns:
                None.
            """
            try:
                token = get_leg_api_token()
            except ConfigError as exc:
                ui.notify(str(exc), type="negative", timeout=8000)
                return
            try:
                with connection_scope() as connection:
                    result = sync_registrations(connection, token)
            except (CloudflareAuthError, CloudflareApiError) as exc:
                ui.notify(str(exc), type="negative", timeout=8000)
                return
            ui.notify(
                f"{result.created} neu, {result.updated} aktualisiert, {result.unchanged} unverändert.",
                type="positive",
            )
            for warning in result.warnings:
                ui.notify(warning, type="warning", timeout=8000)
            refresh()

        refresh()
