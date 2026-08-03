"""Page modules, one per navigation entry. Importing this package registers
all `@ui.page` routes with NiceGUI.
"""

from app.gui.pages import (  # noqa: F401
    abrechnung,
    auswertungen,
    backup,
    dashboard,
    einstellungen,
    import_page,
    teilnehmer,
    zaehler,
    zuordnungen,
)
