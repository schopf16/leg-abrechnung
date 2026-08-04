"""Import page: select one or many EBIX (.xml) / CSV files, then explicitly
start the import with visible per-file progress.
"""

import asyncio
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
            "Rückfallebene. Dateien auswählen (im Dateidialog mit Strg+A "
            "bzw. Umschalt+Klick den gesamten Ordnerinhalt markieren) und "
            "danach auf „Import starten“ klicken. Ein mehrfacher Import "
            "derselben Periode dupliziert keine Werte."
        ).classes("text-body2 text-grey-8")

        upload_widget = ui.upload(
            label="1. Dateien auswählen (Mehrfachauswahl möglich)",
            multiple=True,
            auto_upload=False,
        ).props('accept=".xml,.csv" hide-upload-btn').classes("w-full")

        start_button = ui.button("2. Import starten", icon="play_arrow").classes("mt-2")

        progress_column = ui.column().classes("w-full mt-4")
        progress_label = ui.label().classes("text-body2")
        progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
        progress_column.visible = False

        result_column = ui.column().classes("w-full mt-4")
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
            """Import every selected file, showing live per-file progress.

            Args:
                event: NiceGUI multi-upload event carrying all selected files.

            Returns:
                None.
            """
            files = event.files
            total = len(files)
            results: list[tuple[str, str]] = []

            start_button.disable()
            progress_column.visible = True
            result_column.clear()

            for index, file in enumerate(files, start=1):
                progress_label.text = f"Importiere Datei {index} von {total}: {file.name}"
                progress_bar.value = (index - 1) / total
                # Yield control so the progress update is actually sent to
                # the browser before the (blocking) import work below runs.
                await asyncio.sleep(0)

                data = await file.read()
                results.append(import_one_file(file.name, data))
                progress_bar.value = index / total
                await asyncio.sleep(0)

            progress_label.text = f"Fertig: {total} von {total} Datei(en) verarbeitet."
            start_button.enable()
            upload_widget.reset()

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

        upload_widget.on_multi_upload(handle_multi_upload)
        start_button.on_click(lambda: upload_widget.run_method("upload"))

        ui.label("Importhistorie").classes("text-lg font-bold mt-6")
        refresh_history()
