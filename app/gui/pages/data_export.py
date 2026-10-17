"""Data export page: all LEG data as CSV files in one ZIP."""

import asyncio
import os
from datetime import datetime
from pathlib import Path

from nicegui import ui

from app.db.connection import connection_scope
from app.export.data_export import FORMAT_VERSION, TABLES, export_data
from app.format_size import format_size
from app.gui.navigation import page_frame
from app.gui.safe_notify import safe_notify
from app.paths import OUTPUT_DIR
from app.version import APP_VERSION

EXPORT_DIR = OUTPUT_DIR / "datenexport"


def _run_export() -> tuple[Path, dict]:
    """Write the ZIP into the export folder (own connection: runs in a worker thread)."""
    target = EXPORT_DIR / f"leg_datenexport_{datetime.now():%Y-%m-%d_%H%M%S}.zip"
    with connection_scope() as connection:
        manifest = export_data(connection, target, app_version=APP_VERSION)
    return target, manifest


@ui.page("/data-export")
def data_export_page() -> None:
    """Render the data export page."""
    with page_frame("/data-export", "Datenexport"):
        ui.label(
            f"Alle Daten der LEG als CSV-Dateien in einer ZIP-Datei (Format {FORMAT_VERSION}), "
            "beschrieben durch die darin enthaltene „manifest.json“. "
            "Die Datei wird in „output/datenexport/“ abgelegt."
        ).classes("text-body2 text-grey-8")
        ui.label(
            "Enthalten: " + ", ".join(t.name for t in TABLES) + ". Nicht enthalten: Korrespondenz, "
            "Textbausteine, Web-Registrierungen, PDF-Dateien und Zugangsdaten."
        ).classes("text-caption text-grey-6")

        result = ui.column().classes("w-full mt-2")

        async def do_export() -> None:
            """Export in a worker thread so the window stays responsive."""
            button.disable()
            try:
                target, manifest = await asyncio.to_thread(_run_export)
            except Exception as exc:  # noqa: BLE001 -- shown to the administrator
                safe_notify(f"Export fehlgeschlagen: {exc}", type="negative")
                return
            finally:
                button.enable()
            result.clear()
            with result:
                ui.label(f"Erstellt: {target.name} ({format_size(target.stat().st_size)})").classes(
                    "font-bold"
                )
                ui.table(
                    columns=[
                        {"name": "file", "label": "Datei", "field": "file", "align": "left"},
                        {"name": "rows", "label": "Zeilen", "field": "rows", "align": "right"},
                    ],
                    rows=[{"file": t["file"], "rows": t["row_count"]} for t in manifest["tables"]],
                    row_key="file",
                ).props("dense flat").classes("w-full max-w-lg")
                ui.button("Ordner öffnen", on_click=lambda: _open_folder(target.parent)).props("flat")

        button = ui.button("Daten exportieren", on_click=do_export, color="primary").classes("mt-2")


def _open_folder(folder: Path) -> None:
    """Show the export folder in the system file manager."""
    opener = getattr(os, "startfile", None)
    if opener is None:
        safe_notify(f"Ordner: {folder}", type="info")
        return
    try:
        opener(str(folder))
    except OSError as exc:
        safe_notify(f"Konnte den Ordner nicht öffnen: {exc}", type="negative")
