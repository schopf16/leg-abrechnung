"""Dashboard / Übersicht page.

Structured by what the administrator actually needs to know, in that
order: is anything wrong right now (Handlungsbedarf), what does the
current data look like (Kennzahlen, LEGs im Überblick), and only then --
if there is barely any data yet -- how to get started. Earlier versions
of this page led with a flat welcome paragraph and a fixed set of
counters; this follows the same "status first" structure used throughout
`app.domain.quality_checks`.
"""

from typing import Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.leg_composition import compute_leg_composition
from app.domain.quality_checks import (
    check_assignment_consistency,
    check_leg_assignment,
    check_leg_upgrade_potential,
    check_onboarding_progress,
    check_substation_area_one_sided,
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


def _load_overview(connection) -> dict:
    """Gather everything the dashboard shows in a single pass.

    Args:
        connection: Open SQLite connection.

    Returns:
        A dict with "counts" (headline numbers), "action_items" (list of
        `(message, link)` tuples needing attention -- `link` is a route
        path to jump straight to the object in question, or `None` if no
        detail page exists for it), "legs" (per-LEG summary rows),
        "offene_registrierungen" (count of not-yet-fully-processed Web-Registrierungen)
        and "open_onboardings" (count of in-progress onboarding trackers,
        see `app.models.person_onboarding`) keys.
    """
    substation_areas = substation_area_repo.list_all(connection)
    legs = leg_repo.list_all(connection)
    sites = site_repo.list_all(connection)
    metering_points = metering_point_repo.list_all(connection)
    persons = person_repo.list_all(connection)
    runs = billing_run_repo.list_runs(connection)
    settings = settings_repo.get_settings(connection)
    offene_registrierungen = sum(
        1 for r in web_registration_repo.list_all(connection) if not r.is_fully_processed
    )
    open_onboardings = len(person_onboarding_repo.list_in_progress(connection))

    action_items: list[tuple[str, Optional[str]]] = []
    if not settings.qr_iban.strip():
        action_items.append(
            (
                "QR-IBAN ist in den Einstellungen noch nicht konfiguriert -- "
                "QR-Rechnungen können nicht erzeugt werden.",
                "/einstellungen",
            )
        )
    if not settings.address_street.strip():
        action_items.append(
            ("Absender-Adresse ist in den Einstellungen noch nicht erfasst.", "/einstellungen")
        )

    for warning in check_assignment_consistency(connection):
        action_items.append((warning.message, warning.link))
    for warning in check_leg_assignment(connection):
        action_items.append((warning.message, warning.link))
    for warning in check_onboarding_progress(connection):
        action_items.append((warning.message, warning.link))
    for warning in check_unresolved_bank_transactions(connection):
        action_items.append((warning.message, warning.link))
    for warning in check_substation_area_one_sided(connection):
        action_items.append((warning.message, warning.link))
    for warning in check_leg_upgrade_potential(connection):
        action_items.append((warning.message, warning.link))

    leg_rows = []
    for leg in legs:
        leg_runs = [r for r in runs if r.leg_id == leg.id]
        latest_run = max(leg_runs, key=lambda r: (r.period_year, r.period_quarter), default=None)
        composition = compute_leg_composition(connection, leg.id)
        if composition.is_mixed:
            substation_area_names = ", ".join(t.name for t in composition.substation_areas)
            action_items.append(
                (
                    f"LEG „{leg.name}“ umfasst mehrere Trafokreise ({substation_area_names}) "
                    "-- tieferer BKW-Rabatt möglich.",
                    "/legs",
                )
            )
        leg_rows.append(
            {
                "name": leg.name,
                "metering_points": leg_repo.count_metering_points(connection, leg.id),
                "letzte_abrechnung": (
                    f"Q{latest_run.period_quarter} {latest_run.period_year}"
                    if latest_run
                    else "noch keine"
                ),
            }
        )

    return {
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
        "offene_registrierungen": offene_registrierungen,
        "offene_aufnahmen": open_onboardings,
    }


@ui.page("/")
def dashboard_page() -> None:
    """Render the dashboard page showing an overview of the LEG data.

    Returns:
        None. Registered as the NiceGUI handler for the root route.
    """
    with page_frame("/", "Übersicht"):
        with connection_scope() as connection:
            overview = _load_overview(connection)
        counts = overview["counts"]

        # -- Handlungsbedarf: whatever needs attention, front and centre. --
        has_issues = bool(overview["action_items"]) or overview["offene_registrierungen"] > 0
        with ui.card().classes("w-full " + ("bg-red-1" if has_issues else "bg-green-1")):
            ui.label("Handlungsbedarf").classes("font-bold")
            if overview["action_items"]:
                for message, link in overview["action_items"]:
                    with ui.row().classes("items-baseline gap-2"):
                        ui.label(f"⚠ {message}").classes("text-negative text-body2")
                        if link:
                            ui.link("→ Ansehen", link).classes("text-body2")
                ui.link("→ Details in den Auswertungen", "/auswertungen").classes("text-body2")
            if overview["offene_registrierungen"]:
                with ui.row().classes("items-center gap-2"):
                    ui.label(
                        f"📥 {overview['offene_registrierungen']} offene Web-Registrierung(en)."
                    ).classes("text-body2")
                    ui.link("→ Zu den Web-Registrierungen", "/web-registrierungen").classes(
                        "text-body2"
                    )
            if overview["offene_aufnahmen"]:
                with ui.row().classes("items-center gap-2"):
                    ui.label(
                        f"📋 {overview['offene_aufnahmen']} Aufnahme(n) in Bearbeitung."
                    ).classes("text-body2")
                    ui.link("→ Zu den Aufnahmen", "/onboardings").classes("text-body2")
            if not has_issues:
                ui.label("✓ Keine offenen Punkte.").classes("text-body2")

        # -- Kennzahlen: what the data currently looks like. --
        ui.label("Kennzahlen").classes("text-lg font-bold mt-4")
        with ui.row().classes("gap-4 flex-wrap"):
            for label, key in (
                ("Trafokreise", "substation_areas"),
                ("Standorte", "sites"),
                ("LEGs", "legs"),
                ("Messpunkte", "metering_points"),
                ("Personen", "persons"),
                ("Abrechnungsläufe", "runs"),
            ):
                with ui.card().classes("w-40"):
                    ui.label(str(counts[key])).classes("text-3xl font-bold")
                    ui.label(label)

        # -- LEGs im Überblick: one line per LEG, most-asked-about facts. --
        if overview["legs"]:
            ui.label("LEGs im Überblick").classes("text-lg font-bold mt-6")
            ui.table(
                columns=[
                    {"name": "name", "label": "LEG", "field": "name", "align": "left"},
                    {"name": "metering_points", "label": "Messpunkte", "field": "metering_points", "align": "right"},
                    {"name": "letzte_abrechnung", "label": "Letzte Abrechnung", "field": "letzte_abrechnung", "align": "left"},
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
                    ui.button("Zu den Einstellungen", on_click=lambda: ui.navigate.to("/einstellungen"))
                    ui.button("Trafokreise erfassen", on_click=lambda: ui.navigate.to("/substation-areas")).props(
                        "flat"
                    )
