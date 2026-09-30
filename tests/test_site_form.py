"""Tests for the Standort dialog's Trafokreis dropdown.

A dropdown is the one list in this app that cannot be re-sorted by the
person reading it: there is no "Sortierung" control on a dialog, so the
order it arrives in is the only order there is. That makes it the place
where relying on the repo's `ORDER BY name` actually costs something --
SQLite's BINARY collation puts "TRA19400" ahead of "TRA9365", and the
administrator hunting for a four-digit circuit finds it below every
five-digit one.

Driving the dialog rather than the key function is deliberate. A unit test
on `text_key` passes whether or not anybody calls it here.
"""

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.gui.site_form import open_site_form
from app.models import substation_area as substation_area_repo
from app.models.substation_area import SubstationArea


def _areas(*names: str) -> None:
    """Create the given Trafokreise.

    Through `connection_scope()`, not the `db` fixture: `db` is an in-memory
    database, while the dialog opens its own connection and so reads the
    scratch file `tests/conftest.py` points `connection_scope()` at. Writing
    to `db` here would leave the dropdown empty.

    Args:
        *names: The names, which are what the dropdown shows.

    Returns:
        None.
    """
    with connection_scope() as connection:
        for name in names:
            substation_area_repo.create(
                connection,
                SubstationArea(id=None, name=name, bkw_designation=name, note="", created_at=""),
            )


def _substation_area_options(probe: str) -> list[str]:
    """Open the Standort dialog and read its Trafokreis options, in order.

    Args:
        probe: A unique probe route -- every `ui.page` registers itself, and
            the suite runs across several processes.

    Returns:
        The option labels as the dropdown lists them.
    """
    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        open_site_form()
    selects = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Select" and element._props.get("label") == "Trafokreis"
    ]
    assert len(selects) == 1, f"ein Trafokreis-Auswahlfeld erwartet, {len(selects)} gefunden"
    options = selects[0].options
    return list(options.values()) if isinstance(options, dict) else list(options)


def test_the_dropdown_reads_numbers_as_numbers():
    """The reported case, driven through the dialog.

    BKW's designations run three to five digits, so a text order is wrong
    for most of them rather than for an edge case: on the real data all 34
    circuits were in the wrong place.
    """
    _areas("Trafokreis-TRA19400", "Trafokreis-TRA9365", "Trafokreis-TRA700")

    assert _substation_area_options("/probe-site-form-order") == [
        "Trafokreis-TRA700",
        "Trafokreis-TRA9365",
        "Trafokreis-TRA19400",
    ]


def test_the_dropdown_folds_umlauts_too():
    """The other half of what the repo's `ORDER BY` gets wrong.

    A leading umlaut sorts behind every "Z..." name under SQLite's BINARY
    collation, so this is not a second nicety but the same defect.
    """
    _areas("Zollikofen", "Ärni-Kreis", "Bern")

    assert _substation_area_options("/probe-site-form-umlaut") == [
        "Ärni-Kreis",
        "Bern",
        "Zollikofen",
    ]
