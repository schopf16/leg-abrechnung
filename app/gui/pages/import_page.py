"""Import page: upload an EBIX (.xml) or CSV file with meter readings."""

import tempfile
from pathlib import Path

from nicegui import events, ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.importers.base import ImportValidationError
from app.importers.import_service import import_file
from app.models.reading import list_import_batches


@ui.page("/import")
def import_page() -> None:
    """Render the reading-import page.

    Returns:
        None.
    """
    with page_frame("/import", "Import"):
        ui.label(
            "Messdaten der BKW importieren: EBIX (.xml) bevorzugt, CSV als "
            "Rückfallebene. Ein mehrfacher Import derselben Periode dupliziert "
            "keine Werte."
        ).classes("text-body2 text-grey-8")

        result_column = ui.column().classes("w-full")
        history_table = ui.table(
            columns=[
                {"name": "imported_at", "label": "Importiert am", "field": "imported_at", "align": "left"},
                {"name": "filename", "label": "Datei", "field": "filename", "align": "left"},
                {"name": "format", "label": "Format", "field": "format", "align": "left"},
                {"name": "period", "label": "Periode", "field": "period", "align": "left"},
                {"name": "row_count", "label": "Werte", "field": "row_count", "align": "right"},
            ],
            rows=[],
            row_key="imported_at",
        ).classes("w-full mt-6")

        def refresh_history() -> None:
            """Reload the import history table.

            Returns:
                None.
            """
            with connection_scope() as connection:
                batches = list_import_batches(connection)
            history_table.rows = [
                {
                    "imported_at": b.imported_at.replace("T", " ").split(".")[0],
                    "filename": b.filename,
                    "format": b.format.upper(),
                    "period": f"{b.period_from} - {b.period_to}" if b.period_from else "-",
                    "row_count": b.row_count,
                }
                for b in batches
            ]
            history_table.update()

        def show_outcome(outcome) -> None:
            """Render the result of one import in the result panel.

            Args:
                outcome: `ImportOutcome` returned by `import_file`.

            Returns:
                None.
            """
            result_column.clear()
            with result_column:
                with ui.card().classes("w-full"):
                    ui.label(f"„{outcome.filename}“ importiert ({outcome.format.upper()})").classes(
                        "text-md font-bold"
                    )
                    ui.label(f"{outcome.rows_stored} Werte gespeichert.")
                    if outcome.period_from:
                        ui.label(f"Periode: {outcome.period_from} bis {outcome.period_to}")
                    for warning in outcome.warnings:
                        ui.label(f"⚠ {warning}").classes("text-negative text-body2")

        def handle_upload(event: events.UploadEventArguments) -> None:
            """Handle a file selected via the upload widget: store and import it.

            Args:
                event: NiceGUI upload event carrying the file name and content.

            Returns:
                None.
            """
            suffix = Path(event.name).suffix
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, prefix="leg_import_"
            ) as tmp_file:
                tmp_file.write(event.content.read())
                tmp_path = Path(tmp_file.name)

            try:
                with connection_scope() as connection:
                    outcome = import_file(connection, tmp_path)
                show_outcome(outcome)
                refresh_history()
                ui.notify(f"{outcome.rows_stored} Werte importiert.", type="positive")
            except ImportValidationError as exc:
                ui.notify(f"Import fehlgeschlagen: {exc}", type="negative")
            finally:
                tmp_path.unlink(missing_ok=True)

        ui.upload(
            label="EBIX- oder CSV-Datei auswählen",
            on_upload=handle_upload,
            auto_upload=True,
        ).props('accept=".xml,.csv"').classes("w-full")

        ui.label("Importhistorie").classes("text-lg font-bold mt-6")
        refresh_history()
