"""The one header every detail page starts with: where you are, and the pencil."""

from typing import Callable, Optional

from nicegui import ui


def render_detail_header(
    *,
    list_route: str,
    list_label: str,
    title: str,
    on_edit: Optional[Callable[[], None]] = None,
) -> None:
    """Render the breadcrumb and, if the record can be edited, the pencil."""
    with ui.row().classes("w-full items-center gap-2"):
        ui.link(list_label, list_route).classes("text-primary")
        ui.label("›").classes("text-grey-5")
        ui.label(title).classes("text-grey-8")
        if on_edit is not None:
            # "Bearbeiten" in words rather than a bare pencil: on a list the
            # icon sits in a row of icons and is read from its position,
            # while here it would be the only one on the page.
            ui.button("Bearbeiten", icon="edit", on_click=lambda: on_edit()).props("flat").classes("ml-auto")
