"""Swiss phone formatting in the Person form and on stored values."""

import pytest
from nicegui import Client, ui

from app.domain.phone_format import format_swiss_phone


@pytest.mark.parametrize(
    ("source", "formatted"),
    [
        ("031 123 45 67", "+41 31 123 45 67"),
        ("+41311234567", "+41 31 123 45 67"),
        ("0041 79 123 45 67", "+41 79 123 45 67"),
        ("+49 30 123456", "+49 30 123456"),
        ("031 123", "031 123"),
        ("", ""),
    ],
)
def test_format_swiss_phone(source, formatted):
    assert format_swiss_phone(source) == formatted


def test_person_form_formats_phone_and_iban_on_open_and_blur():
    from app.gui.person_form import open_person_form

    client = Client(ui.page("/probe-person-financial-contact-format")(lambda: None), request=None)
    with client:
        open_person_form(
            prefill={
                "phone": "0311234567",
                "iban": "CH9300762011623852957",
            }
        )
        phone = next(
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Input" and element.label.startswith("Telefon Person 1")
        )
        iban = next(
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Input" and element.label == "IBAN (für Gutschriften)"
        )
        assert phone.value == "+41 31 123 45 67"
        assert iban.value == "CH93 0076 2011 6238 5295 7"

        phone.value = "0791234567"
        phone_blur = next(
            listener.handler for listener in phone._event_listeners.values() if listener.type == "blur"
        )
        phone_blur()
        assert phone.value == "+41 79 123 45 67"

        iban.value = "ch9300762011623852957"
        iban_blurs = [
            listener.handler for listener in iban._event_listeners.values() if listener.type == "blur"
        ]
        for handler in iban_blurs:
            handler()
        assert iban.value == "CH93 0076 2011 6238 5295 7"
