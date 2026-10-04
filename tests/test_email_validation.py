"""Tests for the email check, and for it being asked at the field.

The restraint is the point: this must complain only where something is
certainly wrong. A false complaint about an address that works trains the
administrator to click past the warning, and then the IBAN warning beside it
gets clicked past too.
"""

import pytest
from nicegui import Client, ui

from app.domain.email_validation import validate_email


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "anna@example.invalid",
        "a.b+tag@mail.example.invalid",
        "ANNA@EXAMPLE.INVALID",
        # Legitimate and unusual: no pattern for the local part, on purpose.
        "x!#$%@example.invalid",
    ],
)
def test_nothing_is_said_against_an_address_that_could_work(value):
    """An empty field is valid -- most people have no second address."""
    assert validate_email(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "anna.example.invalid",
        "anna@@example.invalid",
        "@example.invalid",
        "anna@",
        "anna@example",
        "anna meier@example.invalid",
    ],
)
def test_what_is_certainly_wrong_is_named(value):
    """Each of these cannot reach a mailbox, whatever the domain does."""
    message = validate_email(value)

    assert message is not None
    assert message.startswith("E-Mail-Adresse:")


def test_the_check_runs_when_the_field_loses_focus(address_register):
    """Point 16 of the list: the IBAN was the only field that said anything.

    An email mistake is otherwise found at send time, in the middle of a
    quarter going out -- long after the dialog that knew the address was
    closed.
    """
    from app.gui.person_form import open_person_form

    client = Client(ui.page("/probe-person-email-blur")(lambda: None), request=None)
    with client:
        open_person_form()

        field = next(
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Input" and element.label == "E-Mail"
        )
        field.value = "anna.example.invalid"
        blur = next(
            listener.handler for listener in field._event_listeners.values() if listener.type == "blur"
        )
        blur()

    messages = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label"
        and str(getattr(element, "text", "")).startswith("E-Mail-Adresse:")
    ]
    assert messages, "keine Meldung am Feld"


def test_a_sound_address_leaves_the_field_silent(address_register):
    """Silence is the normal case; a message under every field is noise."""
    from app.gui.person_form import open_person_form

    client = Client(ui.page("/probe-person-email-ok")(lambda: None), request=None)
    with client:
        open_person_form()

        field = next(
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Input" and element.label == "E-Mail"
        )
        field.value = "anna@example.invalid"
        blur = next(
            listener.handler for listener in field._event_listeners.values() if listener.type == "blur"
        )
        blur()

    messages = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label"
        and str(getattr(element, "text", "")).startswith("E-Mail-Adresse:")
    ]
    assert messages == []
