"""Page modules, one per navigation entry. Importing this package registers
all `@ui.page` routes with NiceGUI.
"""

from app.gui.pages import (  # noqa: F401
    abrechnung,
    aufnahmen,
    auswertungen,
    backup,
    dashboard,
    einstellungen,
    email_versand,
    import_page,
    legs,
    messpunkte,
    personen,
    standorte,
    statistik,
    trafokreise,
    web_registrierungen,
    zuordnungen,
)
