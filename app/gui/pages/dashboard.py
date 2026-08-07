"""Dashboard / Übersicht page: quick counts and links to common actions."""

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame


def _load_counts() -> dict:
    """Query headline counts shown on the dashboard.

    Returns:
        A dict with keys "persons", "messpunkte" and "runs" holding the
        respective row counts.
    """
    with connection_scope() as connection:
        persons = connection.execute("SELECT COUNT(*) AS n FROM person").fetchone()["n"]
        messpunkte = connection.execute(
            "SELECT COUNT(*) AS n FROM messpunkt"
        ).fetchone()["n"]
        runs = connection.execute(
            "SELECT COUNT(*) AS n FROM billing_runs"
        ).fetchone()["n"]
    return {"persons": persons, "messpunkte": messpunkte, "runs": runs}


@ui.page("/")
def dashboard_page() -> None:
    """Render the dashboard page showing an overview of the LEG data.

    Returns:
        None. Registered as the NiceGUI handler for the root route.
    """
    with page_frame("/", "Übersicht"):
        counts = _load_counts()
        with ui.row().classes("gap-4"):
            for label, key in (
                ("Personen", "persons"),
                ("Messpunkte", "messpunkte"),
                ("Abrechnungsläufe", "runs"),
            ):
                with ui.card().classes("w-48"):
                    ui.label(str(counts[key])).classes("text-3xl font-bold")
                    ui.label(label)

        ui.separator().classes("my-4")
        ui.label("Willkommen bei der LEG-Abrechnung.").classes("text-lg")
        ui.markdown(
            "Verwalten Sie links Personen, Standorte, Messpunkte, "
            "LEGs und deren Zuordnungen, importieren Sie Messdaten "
            "und erstellen Sie darauf basierend quartalsweise Rechnungen "
            "und Gutschriften."
        )

        if counts["persons"] == 0:
            with ui.card().classes("mt-4 bg-blue-1"):
                ui.label("Erste Schritte").classes("font-bold")
                ui.label(
                    "Es sind noch keine Personen erfasst. Legen Sie unter "
                    "„Personen“, „Standorte“ und „Messpunkte“ Ihre "
                    "Stammdaten an, oder erzeugen Sie unter „Einstellungen“ "
                    "Demo-Daten, um die App auszuprobieren."
                )
                with ui.row().classes("gap-2 mt-2"):
                    ui.button("Zu den Einstellungen", on_click=lambda: ui.navigate.to("/einstellungen"))
                    ui.button("Personen erfassen", on_click=lambda: ui.navigate.to("/personen")).props(
                        "flat"
                    )
