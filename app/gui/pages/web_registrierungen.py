"""Web-Registrierungen page: inbox for registrations submitted through the
public form on leg-ittigen.ch (see `app.importers.registration_sync`).

Does not touch Messpunkt/Zuordnung: matching a reported meter against a
(possibly new) Messpunkt needs a Standort/LEG/Messrichtung judgment call
that stays a manual step in `/messpunkte`/`/zuordnungen`. For Person,
though, the form fields map almost 1:1 -- "Person übernehmen" opens the
normal Person-creation dialog prefilled from the registration (see
`app.gui.person_form`), marks the registration reviewed once the person
is saved, and starts that person's onboarding tracker (see
`app.models.person_onboarding`) with step 1 dated from the registration.
"""

from datetime import date, datetime
from typing import Optional

from nicegui import ui

from app.config import ConfigError, get_leg_api_token
from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.gui.person_form import open_person_form
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.importers.cloudflare_client import (
    CloudflareApiError,
    CloudflareAuthError,
    delete_submissions,
)
from app.importers.registration_sync import sync_registrations
from app.models import messpunkt as messpunkt_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import web_registration as web_registration_repo
from app.models.web_registration import WebRegistration


#: `(label, field)` pairs for the printed table.
PRINT_COLUMNS = [
    ("Name", "name"),
    ("Eingegangen", "eingegangen"),
    ("E-Mail", "email"),
    ("Telefon", "telefon"),
    ("Adresse", "adresse"),
    ("BKW-Kundennummer", "bkw_kundennummer"),
    ("Zähler", "zaehler"),
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
        "name": reg.anzeige_name,
        "eingegangen": reg.submitted_at,
        "email": reg.email,
        "telefon": reg.telefon,
        "adresse": f"{reg.strasse} {reg.hausnummer}, {reg.plz} {reg.ort}".strip(", "),
        "bkw_kundennummer": reg.bkw_kundennummer,
        "zaehler": ", ".join(m.meter_number for m in reg.meters) or "-",
        "status": "Geprüft" if not reg.needs_review else "Zu prüfen",
    }


def _parse_bkw_kundennummer(value: str) -> Optional[int]:
    """Try to interpret a registration's free-text BKW-Kundennummer as an
    integer, for prefilling `Person.bkw_kundennummer` (which is validated).

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


@ui.page("/web-registrierungen")
def web_registrierungen_page() -> None:
    """Render the Web-Registrierungen inbox page.

    Returns:
        None.
    """
    with page_frame("/web-registrierungen", "Web-Registrierungen"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Registrierungen, die Interessierte über das Anmeldeformular "
                "auf leg-ittigen.ch eingereicht haben -- pro Person/Firma ein "
                "Eintrag, mit allen dabei gemeldeten Zählern. Die Übernahme in "
                "Personen/Messpunkte/Zuordnungen bleibt ein manueller Schritt "
                "in den jeweiligen Ansichten -- hier dienen die Angaben nur "
                "als Vorlage."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    rubrik="Web-Registrierungen",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: [_print_row(r) for r in visible_regs],
                    get_filter_description=lambda: (
                        "inkl. geprüfte" if show_reviewed_switch.value else None
                    ),
                )
                ui.button("Registrierungen abrufen", on_click=lambda: do_sync())

        show_reviewed_switch = ui.switch("Auch geprüfte anzeigen")
        list_container = ui.column().classes("w-full gap-2 mt-2")

        visible_regs: list[WebRegistration] = []

        def render_card(reg: WebRegistration, known_messpunkte: set[str]) -> None:
            """Render one registration as a card with wrapping field groups.

            Args:
                reg: Registration to render.
                known_messpunkte: All existing Messpunkt-Bezeichnungen, to
                    flag which of `reg.meters` already have a matching
                    Messpunkt (informational only).

            Returns:
                None.
            """
            with ui.card().classes("w-full" + ("" if reg.needs_review else " opacity-60")):
                with ui.row().classes("w-full items-start gap-6 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[200px]"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(reg.anzeige_name or "-").classes("font-bold")
                            if not reg.needs_review:
                                ui.badge("Geprüft", color="grey")
                            if reg.person_created:
                                ui.badge("Person angelegt", color="positive")
                        ui.label(f"Eingegangen: {reg.submitted_at}").classes(
                            "text-caption text-grey-6"
                        )
                    with ui.column().classes("gap-0 min-w-[180px]"):
                        ui.label(reg.email or "-")
                        ui.label(reg.telefon or "-").classes("text-grey-7")
                    with ui.column().classes("gap-0 min-w-[200px]"):
                        ui.label(f"{reg.strasse} {reg.hausnummer}".strip() or "-")
                        ui.label(f"{reg.plz} {reg.ort}".strip() or "-")
                    with ui.column().classes("gap-0 min-w-[180px]"):
                        ui.label(f"BKW-Kundennummer: {reg.bkw_kundennummer or '-'}")
                        ui.label(f"IBAN: {reg.iban or '-'}").classes("text-grey-7")
                    with ui.column().classes("gap-0 min-w-[220px]"):
                        ui.label("Gemeldete Zähler:").classes("text-caption text-grey-6")
                        if reg.meters:
                            for meter in reg.meters:
                                label = meter.meter_number + (
                                    f" ({meter.note})" if meter.note else ""
                                )
                                with ui.row().classes("items-center gap-1"):
                                    ui.label(label).classes("font-mono text-caption")
                                    if meter.meter_number in known_messpunkte:
                                        ui.badge("Messpunkt vorhanden", color="primary")
                        else:
                            ui.label("keine").classes("text-caption text-grey-6")
                    with ui.column().classes("gap-0 min-w-[200px]"):
                        ui.label(reg.message or "-").classes("text-grey-7")
                    with ui.column().classes("gap-1 ml-auto items-end"):
                        # "Person übernehmen" stays available even once
                        # reviewed -- e.g. this entry was dismissed before
                        # that took over a person, and now should.
                        ui.button(
                            "Person übernehmen",
                            on_click=lambda r=reg: on_take_over(r),
                        ).props("dense flat color=primary")
                        if reg.needs_review:
                            ui.button(
                                "Als geprüft markieren",
                                on_click=lambda r=reg: on_mark_reviewed(r),
                            ).props("dense flat")
                        ui.button(
                            "Löschen", on_click=lambda r=reg: on_delete(r)
                        ).props("dense flat color=negative")

        def refresh() -> None:
            """Reload the registrations list according to the current filter.

            Returns:
                None.
            """
            nonlocal visible_regs
            with connection_scope() as connection:
                regs = (
                    web_registration_repo.list_all(connection)
                    if show_reviewed_switch.value
                    else web_registration_repo.list_needs_review(connection)
                )
                known_messpunkte = {
                    mp.messpunkt_bezeichnung for mp in messpunkt_repo.list_all(connection)
                }
            visible_regs = regs
            list_container.clear()
            with list_container:
                if not regs:
                    ui.label(
                        "Keine Registrierungen."
                        if show_reviewed_switch.value
                        else "Keine offenen Registrierungen."
                    )
                for reg in regs:
                    render_card(reg, known_messpunkte)

        show_reviewed_switch.on_value_change(lambda _: refresh())

        def on_take_over(reg: WebRegistration) -> None:
            """Card button handler: open a prefilled Person-creation dialog.

            The registration is marked reviewed automatically once the
            person is actually saved (not just when the dialog is opened)
            -- if the administrator cancels, the registration stays open.

            Args:
                reg: Registration to take over.

            Returns:
                None.
            """
            prefill = {
                "firma": reg.firma,
                "anrede": reg.anrede,
                "vorname": reg.vorname,
                "nachname": reg.nachname,
                "strasse": reg.strasse,
                "hausnummer": reg.hausnummer,
                "plz": reg.plz,
                "ort": reg.ort,
                "email": reg.email,
                "telefon": reg.telefon,
                "iban": reg.iban,
            }
            bkw_kundennummer = _parse_bkw_kundennummer(reg.bkw_kundennummer)
            if bkw_kundennummer is not None:
                prefill["bkw_kundennummer"] = bkw_kundennummer

            def on_person_saved(saved_person) -> None:
                with connection_scope() as connection:
                    web_registration_repo.mark_reviewed(connection, reg.id)
                    web_registration_repo.mark_person_created(connection, reg.id)
                    person_onboarding_repo.start_for_person(
                        connection,
                        saved_person.id,
                        angemeldet_am=_parse_submitted_date(reg.submitted_at),
                    )
                refresh()

            open_person_form(prefill=prefill, on_saved=on_person_saved)

        def on_mark_reviewed(reg: WebRegistration) -> None:
            """Card button handler: mark a registration as reviewed.

            Args:
                reg: Registration to mark.

            Returns:
                None.
            """
            with connection_scope() as connection:
                web_registration_repo.mark_reviewed(connection, reg.id)
            # notify before refresh(): see app.gui.safe_notify -- refresh()
            # rebuilds list_container, which can tear down this card (and
            # the button that triggered this handler) before notify runs.
            safe_notify("Als geprüft markiert.", type="positive")
            refresh()

        def on_delete(reg: WebRegistration) -> None:
            """Card button handler: delete a registration after confirmation,
            both locally and from the remote leg-ittigen.ch Worker database.

            Args:
                reg: Registration to delete.

            Returns:
                None.
            """
            with ui.dialog() as confirm, ui.card():
                ui.label(f'"{reg.anzeige_name or reg.email}" wirklich löschen?').classes(
                    "font-bold"
                )
                if not reg.person_created:
                    ui.label(
                        "⚠ Diese Registrierung wurde noch nicht als Person "
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
                            ui.notify(str(exc), type="negative", timeout=8000)
                            return
                        try:
                            delete_submissions([reg.cloudflare_id], token)
                        except (CloudflareAuthError, CloudflareApiError) as exc:
                            confirm.close()
                            ui.notify(str(exc), type="negative", timeout=8000)
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
                f"{result.neu} neu, {result.aktualisiert} aktualisiert, "
                f"{result.unveraendert} unverändert.",
                type="positive",
            )
            for warning in result.warnings:
                ui.notify(warning, type="warning", timeout=8000)
            refresh()

        refresh()
