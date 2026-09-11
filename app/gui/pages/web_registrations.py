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

For each of the three, the card also shows whether a matching record
already exists in the app *without* having been taken over here (e.g. an
existing customer resubmitted the form, or the administrator already
created it by hand) -- see `_registration_status` -- so nothing gets
duplicated. There is no separate "reviewed" flag: an entry with nothing
left to take over (`WebRegistration.is_fully_processed`) simply has
nothing more to do here, and can be deleted once truly obsolete.
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


@dataclass
class _RegistrationStatus:
    """Whether a matching record already exists for one registration's
    Person/site/each reported MeteringPoint, *without* having been taken
    over via this page -- purely informational, see module docstring.

    Attributes:
        person_exists: A Person with this registration's email already exists.
        site_exists: A site with this registration's address already exists.
        metering_point_exists: `{meter.id: bool}` for each of the registration's meters.
    """

    person_exists: bool
    site_exists: bool
    metering_point_exists: dict[int, bool]


def _registration_status(
    reg: WebRegistration,
    known_person_emails: set[str],
    known_site_addresses: set[tuple[str, str, str]],
    known_metering_points: set[str],
) -> _RegistrationStatus:
    """Compute one registration's "does a match already exist?" status.

    Args:
        reg: Registration to check.
        known_person_emails: Every existing Person's non-empty `contact_email`.
        known_site_addresses: Every existing site's
            `(street, house_number, postal_code)`, lowercased.
        known_metering_points: Every existing MeteringPoint's `designation`.

    Returns:
        The computed `_RegistrationStatus`.
    """
    site_key = (reg.street.strip().lower(), reg.house_number.strip().lower(), reg.postal_code.strip().lower())
    return _RegistrationStatus(
        person_exists=bool(reg.email) and reg.email in known_person_emails,
        site_exists=site_key in known_site_addresses,
        metering_point_exists={m.id: m.meter_number in known_metering_points for m in reg.meters},
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

    Args:
        value: Raw `submitted_at` value from the registration.

    Returns:
        The parsed date, or today's date if `value` could not be parsed.
    """
    try:
        return datetime.fromisoformat(value.replace(" ", "T")).date()
    except ValueError:
        return date.today()


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
                )
                ui.button("Registrierungen abrufen", on_click=lambda: do_sync())

        show_complete_switch = ui.switch("Auch vollständig übernommene anzeigen")
        list_container = ui.column().classes("w-full gap-2 mt-2")

        visible_regs: list[WebRegistration] = []

        def _take_over_row(label: str, *, done: bool, exists: bool, on_click: Callable[[], None]) -> None:
            """Render one "... übernehmen" button with its status badge.

            Args:
                label: Button text, e.g. "Person übernehmen".
                done: Whether this item was already taken over.
                exists: Whether a matching record already exists without
                    having been taken over (ignored if `done`).
                on_click: Handler for the button.

            Returns:
                None.
            """
            with ui.row().classes("items-center gap-2"):
                ui.button(label, on_click=on_click).props("dense flat color=primary size=sm")
                if done:
                    ui.badge("übernommen", color="positive")
                elif exists:
                    ui.badge("existiert bereits", color="info")

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
                        "Person übernehmen",
                        done=reg.person_created,
                        exists=status.person_exists,
                        on_click=lambda r=reg: on_take_over_person(r),
                    )
                    _take_over_row(
                        "Standort übernehmen",
                        done=reg.site_created,
                        exists=status.site_exists,
                        on_click=lambda r=reg: on_take_over_site(r),
                    )
                    if reg.meters:
                        for meter in reg.meters:
                            with ui.row().classes("items-center gap-2"):
                                meter_label = meter.meter_number + (f" ({meter.note})" if meter.note else "")
                                ui.label(meter_label).classes("font-mono text-caption min-w-[160px]")
                                _take_over_row(
                                    "Messpunkt übernehmen",
                                    done=meter.metering_point_created,
                                    exists=status.metering_point_exists.get(meter.id, False),
                                    on_click=lambda r=reg, m=meter: on_take_over_metering_point(r, m),
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
                known_person_emails = {
                    p.contact_email for p in person_repo.list_all(connection) if p.contact_email
                }
                known_site_addresses = {
                    (s.street.strip().lower(), s.house_number.strip().lower(), s.postal_code.strip().lower())
                    for s in site_repo.list_all(connection)
                }
                known_metering_points = {mp.designation for mp in metering_point_repo.list_all(connection)}
            regs = (
                all_regs if show_complete_switch.value else [r for r in all_regs if not r.is_fully_processed]
            )
            visible_regs = regs
            list_container.clear()
            with list_container:
                if not regs:
                    ui.label(
                        "Keine Registrierungen."
                        if show_complete_switch.value
                        else "Keine offenen Registrierungen."
                    )
                for reg in regs:
                    render_card(
                        reg,
                        _registration_status(
                            reg, known_person_emails, known_site_addresses, known_metering_points
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
                    web_registration_repo.mark_person_created(connection, reg.id)
                    person_onboarding_repo.start_for_person(
                        connection,
                        saved_person.id,
                        registered_at=_parse_submitted_date(reg.submitted_at),
                    )
                refresh()

            open_person_form(prefill=prefill, on_saved=on_person_saved)

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
                    web_registration_repo.mark_site_created(connection, reg.id)
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
                    web_registration_repo.mark_metering_point_created(connection, meter.id)
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
