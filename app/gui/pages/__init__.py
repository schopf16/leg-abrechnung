"""Page modules, one per navigation entry. Importing this package registers
all `@ui.page` routes with NiceGUI.
"""

from app.gui.pages import (  # noqa: F401
    address_register,
    billing,
    onboardings,
    offboardings,
    reports,
    backup,
    dashboard,
    receivables,
    settings,
    email_dispatch,
    import_page,
    legs,
    dunning,
    message_templates,
    metering_points,
    persons,
    signatures,
    sites,
    statistics,
    substation_areas,
    web_registrations,
    assignments,
)
