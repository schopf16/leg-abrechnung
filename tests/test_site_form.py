"""Tests for the Standort dialog's Trafokreis dropdown."""

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.gui.site_form import open_site_form
from app.models import substation_area as substation_area_repo
from app.models.substation_area import SubstationArea


def _areas(*names: str) -> None:
    """Create the given Trafokreise."""
    with connection_scope() as connection:
        for name in names:
            substation_area_repo.create(
                connection,
                SubstationArea(id=None, name=name, bkw_designation=name, note="", created_at=""),
            )


def _substation_area_options(probe: str) -> list[str]:
    """Open the Standort dialog and read its Trafokreis options, in order."""
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
    """The reported case, driven through the dialog."""
    _areas("Trafokreis-TRA19400", "Trafokreis-TRA9365", "Trafokreis-TRA700")

    assert _substation_area_options("/probe-site-form-order") == [
        "Trafokreis-TRA700",
        "Trafokreis-TRA9365",
        "Trafokreis-TRA19400",
    ]


def test_the_dropdown_folds_umlauts_too():
    """The other half of what the repo's `ORDER BY` gets wrong."""
    _areas("Zollikofen", "Ärni-Kreis", "Bern")

    assert _substation_area_options("/probe-site-form-umlaut") == [
        "Ärni-Kreis",
        "Bern",
        "Zollikofen",
    ]
