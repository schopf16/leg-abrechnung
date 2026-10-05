"""Tests that drive the Standort dialog itself, not the widget behind it."""

from nicegui import Client, ui

from app.gui.site_form import open_site_form


def _dialog() -> Client:
    """Open a new Standort dialog."""
    client = Client(ui.page("/probe-site-address")(lambda: None), request=None)
    with client:
        open_site_form()
    return client


def _fields(client: Client) -> dict:
    """The dialog's inputs, by their German label."""
    return {
        element._props.get("label"): element
        for element in client.elements.values()
        if element.__class__.__name__ == "Input"
    }


def _hints(client: Client) -> list[str]:
    """Every address hint currently shown."""
    return [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label"
        and getattr(element, "text", "")
        and ("Meinten Sie" in element.text or "amtlichen Verzeichnis" in element.text)
    ]


def _suggestions(client: Client) -> int:
    """How many suggestions the floating lists hold."""
    return sum(
        1
        for menu in client.elements.values()
        if menu.__class__.__name__ == "Menu"
        for child in menu.descendants()
        if child.__class__.__name__ == "Button"
    )


def test_a_new_dialog_says_nothing(address_register):
    """An empty form has no address to complain about."""
    client = _dialog()

    assert _hints(client) == []
    assert _suggestions(client) == 0


def test_typing_a_street_offers_suggestions_and_no_complaint(address_register):
    """Help while typing, and only help."""
    client = _dialog()
    fields = _fields(client)

    fields["Adresse"].value = "Erst"

    assert _suggestions(client) >= 1
    assert _hints(client) == []


def test_a_half_typed_address_is_not_called_wrong(address_register):
    """Street typed, postal code still empty -- nothing can be said yet."""
    client = _dialog()
    fields = _fields(client)

    fields["Adresse"].value = "Erstweg"

    assert _hints(client) == []


def test_a_missing_house_number_is_not_called_wrong(address_register):
    """It is "not typed yet", not "wrong"."""
    client = _dialog()
    fields = _fields(client)
    fields["PLZ"].value = "3048"
    fields["Ort"].value = "Musterdorf"

    fields["Adresse"].value = "Erstweg"

    assert _hints(client) == []


def test_a_complete_and_correct_address_stays_silent(address_register):
    """Silence is the normal case."""
    client = _dialog()
    fields = _fields(client)
    fields["PLZ"].value = "3048"
    fields["Ort"].value = "Musterdorf"
    fields["Hausnummer"].value = "4"

    fields["Adresse"].value = "Erstweg"

    assert _hints(client) == []


def test_a_wrong_locality_is_asked_about_at_the_field(address_register):
    """And this is what the hint is for."""
    client = _dialog()
    fields = _fields(client)
    fields["PLZ"].value = "3048"
    fields["Hausnummer"].value = "4"
    fields["Adresse"].value = "Erstweg"

    fields["Ort"].value = "Grossgemeinde"

    assert _hints(client) == ["Meinten Sie: Musterdorf?"]


def test_the_postal_code_in_the_form_ranks_the_street_suggestions(address_register):
    """The administrator's case: the fitting street sat in seventh place."""
    client = _dialog()
    fields = _fields(client)
    fields["PLZ"].value = "3065"

    fields["Adresse"].value = "Drittweg"

    assert _suggestions(client) >= 1


def test_the_dialog_works_without_a_register():
    """No register, no suggestions, no hints, no error."""
    client = _dialog()
    fields = _fields(client)

    fields["Adresse"].value = "Erstweg"

    assert _suggestions(client) == 0
    assert _hints(client) == []


# --- Getting out of the way -----------------------------------------------


def test_the_cursor_starts_in_the_address_field(address_register):
    """So the dialog can be typed into without reaching for the mouse."""
    fields = _fields(_dialog())

    assert fields["Adresse"]._props.get("autofocus") is True
    assert all(
        element._props.get("autofocus") is not True for label, element in fields.items() if label != "Adresse"
    ), "genau ein Feld darf den Fokus beanspruchen"


def test_the_address_fields_do_not_delay_the_lookup(address_register):
    """Typing a street quickly and stopping left the list empty until another key was pressed seconds..."""
    fields = _fields(_dialog())

    for label in ("Adresse", "Hausnummer", "PLZ", "Ort"):
        assert fields[label]._props.get("debounce") in (0, "0"), label
