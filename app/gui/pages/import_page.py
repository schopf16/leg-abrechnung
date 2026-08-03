"""Import page: upload one or many EBIX (.xml) / CSV files with meter readings."""

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
            "Rückfallebene. Es können mehrere Dateien auf einmal ausgewählt "
            "werden (im Dateidialog mit Strg+A bzw. Umschalt+Klick den "
            "gesamten Ordnerinhalt markieren). Ein mehrfacher Import "
            "derselben Periode dupliziert keine Werte."
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

        def import_one_file(filename: str, data: bytes) -> tuple[str, str]:
            """Import a single already-read file's bytes.

            Args:
                filename: Original filename, used to pick the parser and
                    for display in the result panel.
                data: Raw file content.

            Returns:
                A `(status, message)` tuple, `status` being "ok" or "error".
            """
            # Written into its own temp directory under the original
            # filename (rather than a randomized tempfile name) so the
            # import history shows the real filename, not "leg_import_xyz".
            tmp_dir = Path(tempfile.mkdtemp(prefix="leg_import_"))
            tmp_path = tmp_dir / Path(filename).name
            tmp_path.write_bytes(data)

            try:
                with connection_scope() as connection:
                    outcome = import_file(connection, tmp_path)
            except ImportValidationError as exc:
                return "error", f"{filename}: {exc}"
            finally:
                tmp_path.unlink(missing_ok=True)
                tmp_dir.rmdir()

            message = f"{filename}: {outcome.rows_stored} Werte gespeichert"
            if outcome.period_from:
                message += f" ({outcome.period_from} bis {outcome.period_to})"
            if outcome.warnings:
                message += " -- " + "; ".join(outcome.warnings)
            return "ok", message

        def show_results(results: list[tuple[str, str]]) -> None:
            """Render the outcome of one or more imports in the result panel.

            Args:
                results: `(status, message)` tuples, one per imported file.

            Returns:
                None.
            """
            result_column.clear()
            with result_column:
                with ui.card().classes("w-full"):
                    ok_count = sum(1 for status, _ in results if status == "ok")
                    ui.label(f"{ok_count} von {len(results)} Datei(en) importiert").classes(
                        "text-md font-bold"
                    )
                    for status, message in results:
                        css_class = "text-body2" if status == "ok" else "text-negative text-body2"
                        symbol = "✓" if status == "ok" else "⚠"
                        ui.label(f"{symbol} {message}").classes(css_class)

        async def handle_multi_upload(event: events.MultiUploadEventArguments) -> None:
            """Handle one or more files selected at once via the upload widget.

            Args:
                event: NiceGUI multi-upload event carrying all selected files.

            Returns:
                None.
            """
            results = []
            for file in event.files:
                data = await file.read()
                results.append(import_one_file(file.name, data))

            show_results(results)
            refresh_history()

            ok_count = sum(1 for status, _ in results if status == "ok")
            if ok_count == len(results):
                ui.notify(f"{ok_count} Datei(en) erfolgreich importiert.", type="positive")
            else:
                ui.notify(
                    f"{ok_count} von {len(results)} Datei(en) importiert, Rest fehlgeschlagen.",
                    type="warning",
                )

        ui.upload(
            label="EBIX- oder CSV-Dateien auswählen (Mehrfachauswahl möglich)",
            multiple=True,
            on_multi_upload=handle_multi_upload,
            auto_upload=True,
        ).props('accept=".xml,.csv"').classes("w-full")

        ui.label("Importhistorie").classes("text-lg font-bold mt-6")
        refresh_history()
