"""Backup / restore page."""

from pathlib import Path

from nicegui import ui

from app.backup.backup_service import (
    BackupValidationError,
    create_backup,
    list_backups,
    mirror_backup,
    restore_backup,
)
from app.db.connection import connection_scope
from app.format_size import format_size
from app.gui.navigation import page_frame
from app.gui.safe_notify import safe_notify
from app.models import settings as settings_repo


def _backup_row(info) -> dict:
    """Describe one backup file for the table.

    The counts are what makes two backups tellable apart -- a filename
    and a size say nothing about which state a snapshot holds -- so they
    come before the filename rather than after it.

    Args:
        info: A `app.backup.backup_service.BackupFileInfo`.

    Returns:
        A row dict matching the table's columns.
    """
    contents = info.contents
    return {
        "filename": info.path.name,
        "path": str(info.path),
        "created_at": info.created_at.strftime("%d.%m.%Y %H:%M"),
        # An unreadable backup shows dashes, never zeroes: "no answer"
        # and "an empty community" must not look the same.
        "legs": contents.legs if contents else "?",
        "persons": contents.persons if contents else "?",
        "metering_points": contents.metering_points if contents else "?",
        "size": format_size(info.size_bytes),
        # A file that cannot be restored still appears, carrying the
        # reason -- otherwise "why is my file not in the list" has no
        # answer anywhere.
        "state": "✓ verwendbar" if info.is_usable else f"✗ {info.problem}",
        "usable": info.is_usable,
    }


@ui.page("/backup")
def backup_page() -> None:
    """Render the backup and restore page.

    Returns:
        None.
    """
    with page_frame("/backup", "Backup"):
        ui.label(
            "Ein Backup ist eine einzelne Datei mit der gesamten Datenbank. "
            "Sie wird immer in „backups/“ abgelegt."
        ).classes("text-body2 text-grey-8")

        with connection_scope() as connection:
            current_settings = settings_repo.get_settings(connection)

        with ui.card().classes("w-full max-w-lg mt-2"):
            ui.label("Zusätzlicher Backup-Pfad").classes("font-bold")
            ui.label(
                "Optional: jedes neu erstellte Backup wird zusätzlich in "
                "diesen Ordner kopiert (z. B. ein externes Netzlaufwerk). "
                "Der Pfad bleibt bis auf Änderung unverändert gespeichert. "
                "Ist er gerade nicht erreichbar (z. B. auf Reisen), wird "
                "das nur als Hinweis gemeldet -- das reguläre Backup in "
                "„backups/“ wird davon nicht beeinträchtigt."
            ).classes("text-caption text-grey-6")
            extra_path_input = ui.input(
                "Pfad (leer lassen = keine Kopie)",
                value=current_settings.extra_backup_dir,
            ).classes("w-full")

            def save_extra_path() -> None:
                """Persist the extra backup path setting.

                Returns:
                    None.
                """
                with connection_scope() as connection:
                    settings = settings_repo.get_settings(connection)
                    settings.extra_backup_dir = extra_path_input.value.strip()
                    settings_repo.update_settings(connection, settings)
                ui.notify("Pfad gespeichert.", type="positive")

            ui.button("Pfad speichern", on_click=save_extra_path).classes("mt-2")

        backups_table = ui.table(
            columns=[
                {"name": "created_at", "label": "Erstellt am", "field": "created_at", "align": "left"},
                {"name": "legs", "label": "LEG", "field": "legs", "align": "right"},
                {"name": "persons", "label": "Personen", "field": "persons", "align": "right"},
                {
                    "name": "metering_points",
                    "label": "Messpunkte",
                    "field": "metering_points",
                    "align": "right",
                },
                {"name": "size", "label": "Grösse", "field": "size", "align": "right"},
                {"name": "state", "label": "Zustand", "field": "state", "align": "left"},
                {"name": "filename", "label": "Datei", "field": "filename", "align": "left"},
                {"name": "actions", "label": "", "field": "actions", "align": "right"},
            ],
            rows=[],
            row_key="filename",
        ).classes("w-full mt-4")
        backups_table.add_slot(
            "body-cell-actions",
            r"""
            <q-td :props="props">
                <q-btn v-if="props.row.usable" dense flat label="Wiederherstellen" color="warning"
                       @click="() => $parent.$emit('restore', props.row)" />
            </q-td>
            """,
        )

        def refresh_backups_table() -> None:
            """Reload the list of backup files.

            Returns:
                None.
            """
            backups_table.rows = [_backup_row(b) for b in list_backups()]
            backups_table.update()

        def do_create_backup() -> None:
            """Create a new backup, mirror it to the extra path if configured, and refresh the list.

            Returns:
                None.
            """
            backup_path = create_backup()
            with connection_scope() as connection:
                extra_dir = settings_repo.get_settings(connection).extra_backup_dir
            if extra_dir:
                mirror_warning = mirror_backup(backup_path, Path(extra_dir))
                if mirror_warning:
                    ui.notify(mirror_warning, type="warning", timeout=8000)
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
                ui.label(f'Backup "{filename}" wirklich wiederherstellen?').classes("font-bold")
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
                        # notify before refresh_backups_table() -- see
                        # app.gui.safe_notify's module docstring for why a
                        # plain ui.notify() here can raise "parent element
                        # ... has been deleted" once the dialog it was
                        # called from is gone.
                        safe_notify(
                            "Wiederherstellung abgeschlossen. Sicherheits-Backup: "
                            f"{result.safety_backup_path.name}. Bitte die App neu "
                            "starten.",
                            type="positive",
                            timeout=10000,
                        )
                        refresh_backups_table()

                    ui.button("Wiederherstellen", on_click=do_restore, color="negative")
            confirm.open()

        backups_table.on("restore", on_restore_clicked)

        refresh_backups_table()
