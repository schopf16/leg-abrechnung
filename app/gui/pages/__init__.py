"""Page modules, one per navigation entry. Importing this package registers
all `@ui.page` routes with NiceGUI.
"""

from app.gui.pages import (  # noqa: F401
    abrechnung,
    aufnahmen,
    austritte,
    auswertungen,
    backup,
    dashboard,
    debitoren,
    einstellungen,
    email_versand,
    import_page,
    legs,
    mahnwesen,
    metering_points,
    persons,
    signaturen,
    sites,
    statistik,
    substation_areas,
    web_registrierungen,
    assignments,
)
