"""The one header every detail page starts with: where you are, and the pencil.

Two gaps closed with one piece, because they are the same gap. The list marks
a record with a warning triangle, the eye opens the record -- and on three of
the four detail pages there was **no way to fix anything from there**. Only
the Person page had an edit button, so the loop the markers were built for
(see it in the list, look at it, correct it) ended in a dead end on sites,
metering points and LEGs.

And a detail page said where it was with "← Zurück zu Standorten", which is a
way back rather than a place. A breadcrumb is both: it names the list, it
names the record, and the list half is the link.

Deliberately not a tab bar or a header with a title block: the shape stays
exactly what the four pages already had, one line above the record's own
heading, so nothing below it moves.
"""

from typing import Callable, Optional

from nicegui import ui


def render_detail_header(
    *,
    list_route: str,
    list_label: str,
    title: str,
    on_edit: Optional[Callable[[], None]] = None,
) -> None:
    """Render the breadcrumb and, if the record can be edited, the pencil.

    Args:
        list_route: Route of the list this record belongs to.
        list_label: That list's name, as the drawer spells it.
        title: What the record is called.
        on_edit: Opens the record's edit dialog. Left out where there is
            nothing to edit -- a not-found page, for instance.

    Returns:
        None.
    """
    with ui.row().classes("w-full items-center gap-2"):
        ui.link(list_label, list_route).classes("text-primary")
        ui.label("›").classes("text-grey-5")
        ui.label(title).classes("text-grey-8")
        if on_edit is not None:
            # "Bearbeiten" in words rather than a bare pencil: on a list the
            # icon sits in a row of icons and is read from its position,
            # while here it would be the only one on the page.
            ui.button("Bearbeiten", icon="edit", on_click=lambda: on_edit()).props("flat").classes("ml-auto")
