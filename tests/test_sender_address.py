"""The LEG's own address: its house number, and the correction that ate it.

Reported from use: *"bei der addresse der LEG kann ich keine hausnummer
angeben (die korrektur löscht mir die zahl weg)"*. Street and number shared
one box labelled "Strasse", so the address check compared the whole value
against street names, found nothing, offered the bare street -- and
accepting that wrote it over everything. Migration 54 splits them, which is
the shape `person.billing_house_number` and `site.house_number` already had.

This address is the one that matters most in the app: the creditor on every
QR-bill and the letterhead of every document, entered once and then never
looked at again.
"""

import sqlite3

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.db.schema import _split_sender_house_number, initialize_database
from app.models import settings as settings_repo


def _render_settings(probe: str) -> Client:
    """Render the Allgemein page.

    Args:
        probe: A unique probe route -- every `ui.page` registers itself.

    Returns:
        The client.
    """
    from app.gui.pages.settings import settings_page

    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        settings_page()
    return client


def _field(client: Client, label: str):
    """One input on the page, by its label.

    Args:
        client: The rendered client.
        label: The field's German label.

    Returns:
        The input element.
    """
    return next(
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Input" and element.label == label
    )


def test_the_page_has_a_house_number_field():
    """It had none, which is what was reported."""
    client = _render_settings("/probe-sender-house-number")

    assert _field(client, "Strasse") is not None
    assert _field(client, "Hausnummer") is not None


def test_the_house_number_is_saved_and_comes_back():
    """Driven through the page's own save button, not the repo."""
    client = _render_settings("/probe-sender-save")

    with client:
        _field(client, "Strasse").value = "Untere Zollgasse"
        _field(client, "Hausnummer").value = "28"
        _field(client, "PLZ").value = "3063"
        _field(client, "Ort").value = "Ittigen"
        save = next(
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Button" and element.text == "Speichern"
        )
        next(listener.handler for listener in save._event_listeners.values() if listener.type == "click")(
            None
        )

    with connection_scope() as connection:
        stored = settings_repo.get_settings(connection)
    assert stored.address_street == "Untere Zollgasse"
    assert stored.address_house_number == "28"


def test_the_two_parts_join_again_where_an_address_is_printed(db):
    """Stored apart so each can be checked; joined for the letterhead.

    Mirrors `Person.billing_street_with_number`, which the debtor block of
    the QR-bill has always used.
    """
    settings = settings_repo.get_settings(db)
    settings.address_street = "Untere Zollgasse"
    settings.address_house_number = "28"
    settings_repo.update_settings(db, settings)

    assert settings_repo.get_settings(db).address_street_with_number == "Untere Zollgasse 28"


def test_a_missing_part_leaves_no_stray_space(db):
    """A street without a number must not print a trailing blank."""
    settings = settings_repo.get_settings(db)
    settings.address_street = "Postfach"
    settings.address_house_number = ""
    settings_repo.update_settings(db, settings)

    assert settings_repo.get_settings(db).address_street_with_number == "Postfach"


def test_the_qr_bill_gets_the_two_parts_separately(db):
    """The Swiss standard has them apart and `qrbill` takes them apart.

    This used to cram both into `street`, which the standard does not ask
    for -- splitting them is more correct, not merely tidier.
    """
    import inspect

    from app.pdf import qr_bill_render

    source = inspect.getsource(qr_bill_render)

    assert '"house_num": settings.address_house_number' in source
    assert '"street": settings.address_street,' in source


# --- The one-off split of the existing value ------------------------------


def _settings_row(connection: sqlite3.Connection) -> tuple[str, str]:
    """The stored street and house number.

    Args:
        connection: Open SQLite connection.

    Returns:
        `(street, house_number)`.
    """
    row = connection.execute(
        "SELECT address_street, address_house_number FROM leg_settings WHERE id = 1"
    ).fetchone()
    return row[0], row[1]


def test_an_existing_combined_value_is_split_on_first_open(db):
    """The administrator's address was already typed into the one box."""
    db.execute(
        "UPDATE leg_settings SET address_street = ?, address_house_number = '' WHERE id = 1",
        ("Untere Zollgasse 28",),
    )
    db.commit()

    _split_sender_house_number(db)

    assert _settings_row(db) == ("Untere Zollgasse", "28")


def test_the_split_leaves_a_street_without_a_number_alone(db):
    """ "Postfach" is not a street plus a number."""
    db.execute(
        "UPDATE leg_settings SET address_street = ?, address_house_number = '' WHERE id = 1",
        ("Postfach",),
    )
    db.commit()

    _split_sender_house_number(db)

    assert _settings_row(db) == ("Postfach", "")


def test_the_split_does_not_undo_a_correction(db):
    """Runs only while the number field is empty, so what the administrator
    typed stays."""
    db.execute(
        "UPDATE leg_settings SET address_street = ?, address_house_number = ? WHERE id = 1",
        ("Im Feld 3", "7"),
    )
    db.commit()

    _split_sender_house_number(db)

    assert _settings_row(db) == ("Im Feld 3", "7")


def test_the_split_converges():
    """A second pass finds nothing, so it is safe on every startup."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_database(connection)
    connection.execute(
        "UPDATE leg_settings SET address_street = ?, address_house_number = '' WHERE id = 1",
        ("Sonnenweg 10",),
    )
    connection.commit()

    _split_sender_house_number(connection)
    first = _settings_row(connection)
    _split_sender_house_number(connection)

    assert first == ("Sonnenweg", "10")
    assert _settings_row(connection) == first


def test_the_split_tolerates_a_half_migrated_database():
    """Replaying an old backup reaches this before the column exists."""
    from app.db import schema as schema_module

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    early = [m for m in schema_module.MIGRATIONS if m.version < 21]
    original = schema_module.MIGRATIONS
    try:
        schema_module.MIGRATIONS = early
        schema_module.initialize_database(connection)
    finally:
        schema_module.MIGRATIONS = original

    # Reached without raising, which is the whole assertion.
    _split_sender_house_number(connection)


# --- Every save button on the page actually runs ---------------------------


def test_every_save_button_on_the_page_runs_without_raising():
    """The guard for the defect the administrator met.

    They changed the address, clicked Speichern, and nothing happened --
    because a half-finished edit of mine left the handler referring to a
    field that did not exist yet. NiceGUI runs a synchronous handler and
    swallows its exception, so the click did nothing and said nothing (see
    CLAUDE.md, "A page rendering is not evidence that it works").

    Rendering the page could never have caught that: the button was there
    and looked fine. Pressing every one of them does.
    """
    client = _render_settings("/probe-settings-all-saves")

    buttons = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Button" and element.text == "Speichern"
    ]
    assert len(buttons) >= 4, f"nur {len(buttons)} Speichern-Knöpfe gefunden"

    with client:
        for button in buttons:
            handler = next(
                listener.handler for listener in button._event_listeners.values() if listener.type == "click"
            )
            handler(None)


def test_the_page_no_longer_holds_an_email_text():
    """The invoice and the two dunning texts moved to Textbausteine.

    Pinned because leaving a second place to edit them is exactly the
    "zwei Orte für Vorlagen" the administrator objected to -- and because a
    field left here would be written to a column nothing reads any more.
    """
    client = _render_settings("/probe-settings-no-templates")

    labels = [
        element.label
        for element in client.elements.values()
        if element.__class__.__name__ in {"Input", "Textarea"} and element.label
    ]
    assert "Nachricht" not in labels
    assert labels.count("Betreff") == 0

    headings = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", "")
    ]
    assert "E-Mail-Versand" not in headings


def test_the_dunning_deadlines_stay_on_the_page():
    """Only the texts left. A deadline is not a text."""
    client = _render_settings("/probe-settings-dunning-numbers")

    labels = [
        element.label
        for element in client.elements.values()
        if element.__class__.__name__ == "Number" and element.label
    ]
    assert any("Neue Zahlungsfrist" in label for label in labels)
    assert any("Bagatellgrenze" in label for label in labels)


def test_the_connection_test_moved_to_where_sending_happens():
    """It verifies the Graph credentials without sending anything, so it
    belongs beside the sending, not in the settings."""
    from app.gui.pages.email_dispatch import email_dispatch_page

    settings_client = _render_settings("/probe-settings-no-connection-test")
    settings_buttons = [
        element.text
        for element in settings_client.elements.values()
        if element.__class__.__name__ == "Button" and getattr(element, "text", "")
    ]
    assert "Verbindung testen" not in settings_buttons

    dispatch_client = Client(ui.page("/probe-dispatch-connection-test")(lambda: None), request=None)
    with dispatch_client:
        email_dispatch_page()
    dispatch_buttons = [
        element.text
        for element in dispatch_client.elements.values()
        if element.__class__.__name__ == "Button" and getattr(element, "text", "")
    ]
    assert "Verbindung testen" in dispatch_buttons
