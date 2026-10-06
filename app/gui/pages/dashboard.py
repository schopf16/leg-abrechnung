"""Dashboard / Übersicht page."""

from typing import Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.participant_mix import compute_participant_roles
from app.domain.statistics import installed_capacity_totals
from app.domain.quality_checks import (
    check_assignment_consistency,
    check_leg_assignment,
    check_leg_production_capacity,
    check_cooperative_members_without_shares,
    check_addresses,
    check_feed_in_without_consumption,
    check_offboarding_completed_but_active,
    check_onboarding_progress,
    check_open_billing_cycle,
    check_substation_area_one_sided,
    summarise_warnings,
    check_unresolved_bank_transactions,
)
from app.gui.navigation import page_frame
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models import web_registration as web_registration_repo


#: Every Kennzahlen tile on the overview uses exactly these classes, so a
#: new one cannot quietly come out a different size than the rest.
#: `justify-center` centres the shorter tiles inside the band the row's
#: `items-stretch` gives them.
_TILE_CLASSES = "w-40 justify-center"


def _format_capacity(value: float) -> str:
    """Format a kWp/kWh figure the way a German reader writes it."""
    return f"{value:.2f}".replace(".", ",")


def _load_overview(connection) -> dict:
    """Gather everything the dashboard shows in a single pass."""
    substation_areas = substation_area_repo.list_all(connection)
    legs = leg_repo.list_all(connection)
    sites = site_repo.list_all(connection)
    metering_points = metering_point_repo.list_all(connection)
    # Active only: a person whose offboarding is finished (or who was
    # deactivated because billing history blocks deletion) is history, and
    # the Personen page hides them by default too -- the headline number
    # must match what that page shows.
    persons = [p for p in person_repo.list_all(connection) if p.active]
    runs = billing_run_repo.list_runs(connection)
    settings = settings_repo.get_settings(connection)
    open_registrations = sum(
        1 for r in web_registration_repo.list_all(connection) if not r.is_fully_processed
    )
    open_onboardings = len(person_onboarding_repo.list_in_progress(connection))

    action_items: list[tuple[str, Optional[str]]] = []
    if not settings.qr_iban.strip():
        action_items.append(
            (
                "QR-IBAN ist in den Einstellungen noch nicht konfiguriert -- "
                "QR-Rechnungen können nicht erzeugt werden.",
                "/settings",
            )
        )
    if not settings.address_street.strip():
        action_items.append(("Absender-Adresse ist in den Einstellungen noch nicht erfasst.", "/settings"))

    collected: list = []
    for warning in check_assignment_consistency(connection):
        collected.append(warning)
    for warning in check_leg_assignment(connection):
        collected.append(warning)
    for warning in check_onboarding_progress(connection):
        collected.append(warning)
    for warning in check_offboarding_completed_but_active(connection):
        collected.append(warning)
    for warning in check_cooperative_members_without_shares(connection):
        collected.append(warning)
    for warning in check_feed_in_without_consumption(connection):
        collected.append(warning)
    for warning in check_open_billing_cycle(connection):
        collected.append(warning)
    for warning in check_unresolved_bank_transactions(connection):
        collected.append(warning)
    for warning in check_substation_area_one_sided(connection):
        collected.append(warning)
    for warning in check_leg_production_capacity(connection):
        collected.append(warning)
    for warning in check_addresses(connection):
        collected.append(warning)

    # Collapsed last, so a handful of repeated findings become one line each
    # and the rest of the overview stays visible. Thirteen address lines
    # pushed everything else off the screen.
    for warning in summarise_warnings(collected):
        action_items.append((warning.message, warning.link))

    leg_rows = []
    for leg in legs:
        leg_runs = [r for r in runs if r.leg_id == leg.id]
        latest_run = max(leg_runs, key=lambda r: (r.period_year, r.period_quarter), default=None)
        leg_rows.append(
            {
                "name": leg.name,
                "metering_points": leg_repo.count_metering_points(connection, leg.id),
                "letzte_abrechnung": (
                    f"Q{latest_run.period_quarter} {latest_run.period_year}" if latest_run else "noch keine"
                ),
            }
        )

    return {
        "roles": compute_participant_roles(connection),
        "capacity": installed_capacity_totals(connection),
        "counts": {
            "substation_areas": len(substation_areas),
            "sites": len(sites),
            "legs": len(legs),
            "metering_points": len(metering_points),
            "persons": len(persons),
            "runs": len(runs),
        },
        "action_items": action_items,
        "legs": leg_rows,
        "open_registrations": open_registrations,
        "open_onboardings": open_onboardings,
    }


@ui.page("/")
def dashboard_page() -> None:
    """Render the dashboard page showing an overview of the LEG data."""
    with page_frame("/", "Übersicht"):
        with connection_scope() as connection:
            overview = _load_overview(connection)
        counts = overview["counts"]

        # -- Handlungsbedarf: whatever needs attention, front and centre. --
        has_issues = (
            bool(overview["action_items"])
            or overview["open_registrations"] > 0
            or overview["open_onboardings"] > 0
        )
        with ui.card().classes("w-full " + ("bg-red-1" if has_issues else "bg-green-1")):
            ui.label("Handlungsbedarf").classes("font-bold")
            if overview["action_items"]:
                for message, link in overview["action_items"]:
                    with ui.row().classes("items-baseline gap-2"):
                        ui.label(f"⚠ {message}").classes("text-negative text-body2")
                        if link:
                            ui.link("→ Ansehen", link).classes("text-body2")
                ui.link("→ Details in den Auswertungen", "/reports").classes("text-body2")
            if overview["open_registrations"]:
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"📥 {overview['open_registrations']} offene Web-Registrierung(en).").classes(
                        "text-body2"
                    )
                    ui.link("→ Zu den Web-Registrierungen", "/web-registrations").classes("text-body2")
            if overview["open_onboardings"]:
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"📋 {overview['open_onboardings']} Aufnahme(n) in Bearbeitung.").classes(
                        "text-body2"
                    )
                    ui.link("→ Zu den Aufnahmen", "/onboardings").classes("text-body2")
            if not has_issues:
                ui.label("✓ Keine offenen Punkte.").classes("text-body2")

        # -- Kennzahlen: what the data currently looks like. --
        capacity = overview["capacity"]
        roles = overview["roles"]
        ui.label("Kennzahlen").classes("text-lg font-bold mt-4")
        # `items-stretch` rather than a fixed height: the two capacity tiles
        # carry a third line the counting ones do not, and a hard-coded
        # height would either clip a caption that wraps or need revisiting
        # every time one is reworded. Stretching makes the row settle on its
        # tallest tile by itself.
        with ui.row().classes("gap-4 flex-wrap items-stretch"):
            for label, key in (
                ("Trafokreise", "substation_areas"),
                ("Standorte", "sites"),
                ("LEGs", "legs"),
                ("Messpunkte", "metering_points"),
                ("Personen", "persons"),
                ("Abrechnungsläufe", "runs"),
            ):
                with ui.card().classes(_TILE_CLASSES):
                    ui.label(str(counts[key])).classes("text-3xl font-bold")
                    ui.label(label)

            # People, counted once each: somebody who feeds in and draws
            # is a Prosumer and appears here only there. The two therefore
            # add up, unlike the "9:26" ratio on the LEG pages, which counts
            # metering points and is answering a different question.
            with ui.card().classes(_TILE_CLASSES):
                ui.label(str(roles.prosumers)).classes("text-3xl font-bold")
                ui.label("Prosumer")
                ui.label("Anschlüsse mit Einspeisung und Bezug").classes("text-caption text-grey-6")
                # The caption describes the intended state. Until the gaps
                # are closed a few of these have no Bezug at all, and a
                # tile that quietly included them would not survive the
                # next time somebody adds the numbers up -- which is how
                # this counting unit came to be questioned in the first
                # place. `check_feed_in_without_consumption` names them.
                if roles.feed_in_only:
                    ui.label(f"davon {len(roles.feed_in_only)} ohne Bezug").classes(
                        "text-caption text-orange-9"
                    )
            with ui.card().classes(_TILE_CLASSES):
                ui.label(str(roles.consumers)).classes("text-3xl font-bold")
                ui.label("Konsumer")
                ui.label("Anschlüsse nur mit Bezug").classes("text-caption text-grey-6")

            # Two cards apart from the counting ones, because these are not
            # complete by construction: a capacity is typed in by hand per
            # metering point, so each card says how many it covers. A bare
            # sum would be read as the LEG's total.
            with ui.card().classes(_TILE_CLASSES):
                ui.label(_format_capacity(capacity.pv_kwp)).classes("text-3xl font-bold")
                ui.label("PV-Leistung (kWp)")
                ui.label(f"aus {capacity.pv_counted} von {capacity.pv_expected} Messpunkten").classes(
                    "text-caption text-grey-6"
                )
            with ui.card().classes(_TILE_CLASSES):
                ui.label(_format_capacity(capacity.battery_kwh)).classes("text-3xl font-bold")
                ui.label("Batteriespeicher (kWh)")
                ui.label(f"{capacity.battery_counted} Messpunkte mit Speicher").classes(
                    "text-caption text-grey-6"
                )
        if capacity.implausible:
            ui.label(
                f"⚠ {len(capacity.implausible)} unplausible Angabe(n) nicht mitgezählt: "
                + ", ".join(capacity.implausible)
            ).classes("text-caption text-orange-9 w-full mt-1")

        # -- LEGs im Überblick: one line per LEG, most-asked-about facts. --
        if overview["legs"]:
            ui.label("LEGs im Überblick").classes("text-lg font-bold mt-6")
            ui.table(
                columns=[
                    {"name": "name", "label": "LEG", "field": "name", "align": "left"},
                    {
                        "name": "metering_points",
                        "label": "Messpunkte",
                        "field": "metering_points",
                        "align": "right",
                    },
                    {
                        "name": "letzte_abrechnung",
                        "label": "Letzte Abrechnung",
                        "field": "letzte_abrechnung",
                        "align": "left",
                    },
                ],
                rows=overview["legs"],
                row_key="name",
            ).classes("w-full mt-2")

        # -- Erste Schritte: only relevant while there is barely any data. --
        if counts["persons"] == 0:
            with ui.card().classes("mt-6 bg-blue-1"):
                ui.label("Erste Schritte").classes("font-bold")
                ui.markdown(
                    "Stammdaten in dieser Reihenfolge erfassen:\n\n"
                    "1. **Trafokreise** -- physische Gruppierung durch die BKW\n"
                    "2. **Standorte** -- Netzanschlusspunkte, je einem Trafokreis zugewiesen\n"
                    "3. **LEGs** -- Abrechnungsgruppen\n"
                    "4. **Messpunkte** -- je einem Standort und einer LEG zugewiesen\n"
                    "5. **Personen** -- Bezüger/Produzenten\n"
                    "6. **Zuordnungen** -- welche Person welchen Messpunkt nutzt\n\n"
                    "Alternativ unter „Einstellungen“ Demo-Daten erzeugen, um die App "
                    "auszuprobieren."
                )
                with ui.row().classes("gap-2 mt-2"):
                    ui.button("Zu den Einstellungen", on_click=lambda: ui.navigate.to("/settings"))
                    ui.button(
                        "Trafokreise erfassen", on_click=lambda: ui.navigate.to("/substation-areas")
                    ).props("flat")
