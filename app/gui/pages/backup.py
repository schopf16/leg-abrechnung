"""Backup / restore page."""

from nicegui import ui

from app.backup.backup_service import (
    BackupValidationError,
    create_backup,
    list_backups,
    restore_backup,
)
from app.gui.navigation import page_frame


def _format_size(size_bytes: int) -> str:
    """Format a byte count as a human-readable size.

    Args:
        size_bytes: File size in bytes.

    Returns:
        A string such as "1.3 MB".
    """
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


@ui.page("/backup")
def backup_page() -> None:
    """Render the backup and restore page.

    Returns:
        None.
    """
    with page_frame("/backup", "Backup"):
        ui.label(
            "Ein Backup ist eine einzelne Datei mit der gesamten Datenbank. "
            "Bewahren Sie Backups an einem sicheren, separaten Ort auf (z. B. "
            "einem externen Laufwerk oder Cloud-Speicher)."
        ).classes("text-body2 text-grey-8")

        backups_table = ui.table(
            columns=[
                {"name": "filename", "label": "Datei", "field": "filename", "align": "left"},
                {"name": "created_at", "label": "Erstellt am", "field": "created_at", "align": "left"},
                {"name": "size", "label": "Grösse", "field": "size", "align": "right"},
                {"name": "actions", "label": "", "field": "actions", "align": "right"},
            ],
            rows=[],
            row_key="filename",
        ).classes("w-full mt-4")
        backups_table.add_slot(
            "body-cell-actions",
            r'''
            <q-td :props="props">
                <q-btn dense flat label="Wiederherstellen" color="warning"
                       @click="() => $parent.$emit('restore', props.row)" />
            </q-td>
            ''',
        )

        def refresh_backups_table() -> None:
            """Reload the list of backup files.

            Returns:
                None.
            """
            backups_table.rows = [
                {
                    "filename": b.path.name,
                    "path": str(b.path),
                    "created_at": b.created_at.strftime("%d.%m.%Y %H:%M:%S"),
                    "size": _format_size(b.size_bytes),
                }
                for b in list_backups()
            ]
            backups_table.update()

        def do_create_backup() -> None:
            """Create a new backup and refresh the list.

            Returns:
                None.
            """
            backup_path = create_backup()
            refresh_backups_table()
            ui.notify(f"Backup erstellt: {backup_path.name}", type="positive")

        ui.button("Backup erstellen", on_click=do_create_backup, color="primary").classes("mt-2")

        ui.label("Vorhandene Backups").classes("text-lg font-bold mt-6")

        def on_restore_clicked(event) -> None:
            """Ask for confirmation, then restore the selected backup.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            filename = event.args["filename"]
            path = event.args["path"]

            with ui.dialog() as confirm, ui.card():
                ui.label(f'Backup "{filename}" wirklich wiederherstellen?').classes(
                    "font-bold"
                )
                ui.label(
                    "Die aktuelle Datenbank wird vollständig durch dieses Backup "
                    "ersetzt. Vor dem Ersetzen wird automatisch ein "
                    "Sicherheits-Backup des aktuellen Zustands erstellt."
                ).classes("text-body2")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_restore() -> None:
                        from pathlib import Path

                        try:
                            result = restore_backup(Path(path))
                        except BackupValidationError as exc:
                            ui.notify(str(exc), type="negative")
                            confirm.close()
                            return
                        confirm.close()
                        refresh_backups_table()
                        ui.notify(
                            "Wiederherstellung abgeschlossen. Sicherheits-Backup: "
                            f"{result.safety_backup_path.name}. Bitte die App neu "
                            "starten.",
                            type="positive",
                            timeout=10000,
                        )

                    ui.button("Wiederherstellen", on_click=do_restore, color="negative")
            confirm.open()

        backups_table.on("restore", on_restore_clicked)

        refresh_backups_table()
