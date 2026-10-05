"""Adressregister page: how old the local copy is, and the button to renew it."""

from nicegui import ui

from app.gui.address_register_task import STATE, run_update
from app.gui.navigation import page_frame
from app.gui.safe_notify import safe_notify
from app.importers.address_register import STALE_AFTER_DAYS, read_info

#: German month-less date format used throughout the app's UI.
_DATE_FORMAT = "%d.%m.%Y"


def _format_count(value: int) -> str:
    """Write a row count the way a German reader expects."""
    return f"{value:,}".replace(",", "'")


@ui.page("/address-register")
def address_register_page() -> None:
    """Render the Adressregister page."""
    with page_frame("/address-register", "Adressregister"):
        container = ui.column().classes("w-full")

        def refresh() -> None:
            """Redraw the card from the current register and task state."""
            container.clear()
            with container, ui.card().classes("w-full"):
                info = read_info()

                if info is None:
                    ui.label("Noch nicht heruntergeladen.").classes("text-body1")
                elif info.data_date is None:
                    ui.label("Datenstand unbekannt.").classes("text-body1")
                else:
                    age = info.age_days
                    text = f"Datenstand {info.data_date.strftime(_DATE_FORMAT)}"
                    ui.label(text).classes("text-body1" + (" text-warning" if info.is_stale else ""))
                    if info.is_stale:
                        ui.label(f"{age} Tage alt – aktualisieren.").classes("text-body2 text-warning")

                if info is not None:
                    ui.label(
                        f"{_format_count(info.streets)} Strassen, {_format_count(info.addresses)} Adressen"
                    ).classes("text-body2 text-grey-7")

                if STATE.error:
                    ui.label(STATE.error).classes("text-body2 text-negative")

                progress = ui.linear_progress(value=STATE.progress, show_value=False).classes("w-full")
                progress_label = ui.label().classes("text-body2")
                button = ui.button("Jetzt aktualisieren", icon="cloud_download")

                def tick() -> None:
                    """Mirror the running update into this card."""
                    progress.visible = STATE.running
                    progress_label.visible = STATE.running
                    progress.value = STATE.progress
                    progress_label.text = STATE.label
                    button.set_enabled(not STATE.running)

                tick()
                ui.timer(0.5, tick)

                async def start() -> None:
                    """Run the update, then redraw with the new figures."""
                    if STATE.running:
                        return
                    ok = await run_update()
                    if ok:
                        safe_notify("Adressregister aktualisiert.", type="positive")
                    else:
                        safe_notify(STATE.error or "Aktualisierung nicht möglich.", type="negative")
                    refresh()

                button.on_click(start)

                ui.separator()
                # swisstopo's terms allow free use, redistribution and
                # commercial use; naming the source is the one condition.
                ui.label(
                    "Quelle: Amtliches Verzeichnis der Gebäudeadressen, "
                    "©swisstopo – frei verwendbar unter Quellenangabe."
                ).classes("text-caption text-grey-6")
                ui.label(
                    f"Gilt als aktuell für {STALE_AFTER_DAYS} Tage. "
                    "Die Daten bleiben auf diesem Rechner; es wird keine Adresse abgefragt."
                ).classes("text-caption text-grey-6")

        refresh()
