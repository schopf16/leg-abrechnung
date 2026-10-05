"""Sending a Textbaustein: which one is due, what goes out, and what is logged.

**No test sends a mail.** `graph_client.send_email` is replaced with an
`AsyncMock`, as in `tests/test_email_attachments.py`, and every address is
invented (`example.invalid`).
"""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from nicegui import Client, ui

from app.config import GraphConfig
from app.db.connection import connection_scope
from app.domain import message_attachments
from app.domain.message_templates import due_by_person, due_templates
from app.emailing.person_send import send_person_message
from app.models import leg_document as leg_document_repo
from app.models import message_template as template_repo
from app.models import person as person_repo
from app.models import person_message_log as log_repo
from app.models import person_offboarding as offboarding_repo
from app.models import person_onboarding as onboarding_repo
from app.models.message_template import (
    OCCASION_OFFBOARDING,
    OCCASION_ONBOARDING,
    TRIGGER_STEP_DONE,
    TRIGGER_STEP_PENDING,
    MessageTemplate,
)
from app.models.person import Person

CONFIG = GraphConfig(
    tenant_id="t",
    client_id="c",
    client_secret="s",
    sender_address="absender@example.invalid",
    sender_name="LEG",
)


def _person(db, name: str = "Muster", email: str = "muster@example.invalid") -> Person:
    """Create one person and return it."""
    person_id = person_repo.create(
        db,
        Person(
            id=None,
            salutation="Frau",
            company="",
            first_name="Anna",
            last_name=name,
            contact_email=email,
            contact_phone="",
            billing_street="Beispielweg",
            billing_house_number="1",
            billing_postal_code="3063",
            billing_city="Beispielhausen",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
    )
    return person_repo.get(db, person_id)


def _template(
    db,
    name: str,
    *,
    occasion: str = OCCASION_ONBOARDING,
    step: str = "leg_assigned_at",
    trigger_kind: str = TRIGGER_STEP_DONE,
    deadline_days: int | None = None,
    auto_attachments: list[str] | None = None,
) -> int:
    """Insert one template and return its id."""
    return template_repo.create(
        db,
        MessageTemplate(
            id=None,
            name=name,
            occasion=occasion,
            step=step,
            trigger_kind=trigger_kind,
            deadline_days=deadline_days,
            subject=f"{name} für {{name}}",
            body="{briefanrede}\n\nText.",
            sort_order=10,
            created_at="",
            auto_attachments=list(auto_attachments or []),
        ),
    )


# --- Which baustein is due -------------------------------------------------


def test_a_step_done_baustein_waits_for_the_step(db, no_drafts):
    """Nothing is offered until the step it hangs off has a date."""
    person = _person(db)
    _template(db, "Willkommen", step="leg_assigned_at")
    tracker = onboarding_repo.start_for_person(db, person.id, registered_at=date(2026, 1, 5))

    assert due_templates(db, tracker, OCCASION_ONBOARDING) == []

    tracker.leg_assigned_at = date(2026, 1, 10)
    onboarding_repo.update(db, tracker)

    due = due_templates(db, onboarding_repo.get_by_person(db, person.id), OCCASION_ONBOARDING)
    assert [item.template.name for item in due] == ["Willkommen"]
    assert due[0].step_label == "Einteilung in LEG"
    assert due[0].days_waiting is None, "ein erledigter Schritt zählt keine Tage"


def test_a_step_pending_baustein_waits_for_its_deadline(db, no_drafts):
    """The reminder appears only once the deadline has run out."""
    person = _person(db)
    _template(
        db,
        "Erinnerung",
        step="contract_signed_at",
        trigger_kind=TRIGGER_STEP_PENDING,
        deadline_days=30,
    )
    start = date(2026, 1, 1)
    tracker = onboarding_repo.start_for_person(db, person.id, registered_at=start)

    assert due_templates(db, tracker, OCCASION_ONBOARDING, today=start + timedelta(days=29)) == []

    due = due_templates(db, tracker, OCCASION_ONBOARDING, today=start + timedelta(days=30))
    assert [item.template.name for item in due] == ["Erinnerung"]
    assert due[0].days_waiting == 30


def test_the_deadline_counts_from_the_last_thing_that_happened(db, no_drafts):
    """A later step resets the wait -- the reminder is about the gap since then."""
    person = _person(db)
    _template(
        db,
        "Erinnerung",
        step="contract_signed_at",
        trigger_kind=TRIGGER_STEP_PENDING,
        deadline_days=30,
    )
    tracker = onboarding_repo.start_for_person(db, person.id, registered_at=date(2026, 1, 1))
    tracker.leg_assigned_at = date(2026, 2, 1)
    onboarding_repo.update(db, tracker)
    tracker = onboarding_repo.get_by_person(db, person.id)

    assert due_templates(db, tracker, OCCASION_ONBOARDING, today=date(2026, 2, 20)) == []
    assert due_templates(db, tracker, OCCASION_ONBOARDING, today=date(2026, 3, 3))


def test_a_pending_baustein_without_a_deadline_is_due_at_once(db, no_drafts):
    """No deadline means no waiting."""
    person = _person(db)
    _template(
        db,
        "Sofort",
        step="contract_signed_at",
        trigger_kind=TRIGGER_STEP_PENDING,
        deadline_days=None,
    )
    tracker = onboarding_repo.start_for_person(db, person.id, registered_at=date.today())

    assert [item.template.name for item in due_templates(db, tracker, OCCASION_ONBOARDING)] == ["Sofort"]


def test_a_pending_baustein_disappears_once_the_step_is_done(db, no_drafts):
    """The reminder is about a gap; the gap closed."""
    person = _person(db)
    _template(
        db,
        "Erinnerung",
        step="contract_signed_at",
        trigger_kind=TRIGGER_STEP_PENDING,
        deadline_days=0,
    )
    tracker = onboarding_repo.start_for_person(db, person.id, registered_at=date(2026, 1, 1))
    tracker.contract_signed_at = date(2026, 1, 2)
    onboarding_repo.update(db, tracker)

    assert due_templates(db, onboarding_repo.get_by_person(db, person.id), OCCASION_ONBOARDING) == []


def test_a_baustein_on_an_unknown_step_is_skipped(db, no_drafts):
    """An offboarding step on an onboarding template hangs off nothing."""
    person = _person(db)
    _template(db, "Verirrt", step="bkw_informed_at")
    tracker = onboarding_repo.start_for_person(db, person.id, registered_at=date.today())

    assert due_templates(db, tracker, OCCASION_ONBOARDING) == []


def test_austritte_use_the_same_rule(db, no_drafts):
    """One mechanism for both processes."""
    person = _person(db)
    _template(db, "Austritt bestätigt", occasion=OCCASION_OFFBOARDING, step="metering_point_exit_at")
    tracker = offboarding_repo.start_for_person(db, person.id, reason="voluntary")
    tracker.metering_point_exit_at = date(2026, 6, 30)
    offboarding_repo.update(db, tracker)

    due = due_templates(db, offboarding_repo.get_by_person(db, person.id), OCCASION_OFFBOARDING)
    assert [item.template.name for item in due] == ["Austritt bestätigt"]


def test_the_batch_answer_matches_the_single_one(db, no_drafts):
    """`due_by_person` is an optimisation, not a second rule."""
    person = _person(db)
    _template(db, "Willkommen", step="leg_assigned_at")
    tracker = onboarding_repo.start_for_person(db, person.id, registered_at=date(2026, 1, 1))
    tracker.leg_assigned_at = date(2026, 1, 2)
    onboarding_repo.update(db, tracker)
    tracker = onboarding_repo.get_by_person(db, person.id)

    batch = due_by_person(db, [tracker], OCCASION_ONBOARDING)

    assert [item.template.name for item in batch[person.id]] == [
        item.template.name for item in due_templates(db, tracker, OCCASION_ONBOARDING)
    ]


# --- Sending, and the date that replaces the button ------------------------


def test_a_send_goes_to_one_party_and_is_logged(db):
    """One `sendMail` for the party, both addresses in it, then the log."""
    person = _person(db)
    person.contact_email = "eins@example.invalid"
    person.second_first_name = "Beat"
    person.second_last_name = "Muster"
    person.second_contact_email = "zwei@example.invalid"
    person_repo.update(db, person)
    person = person_repo.get(db, person.id)
    template_id = _template(db, "Willkommen")

    with (
        patch("app.emailing.graph_client.send_email", new=AsyncMock()) as send,
        patch("app.emailing.graph_client.get_access_token", new=AsyncMock(return_value="token")),
    ):
        asyncio.run(
            send_person_message(
                db,
                CONFIG,
                person=person,
                subject="Betreff wie gelesen",
                body="Text wie gelesen",
                occasion=OCCASION_ONBOARDING,
                step="leg_assigned_at",
                template_id=template_id,
            )
        )

    assert send.await_count == 1, "ein sendMail je Vertragspartei"
    sent = send.await_args.kwargs
    assert sorted(sent["to_addresses"]) == ["eins@example.invalid", "zwei@example.invalid"]
    # Verbatim: what was read in the dialog is what goes out.
    assert sent["subject"] == "Betreff wie gelesen"
    assert sent["body"] == "Text wie gelesen"

    logged = log_repo.list_for_person(db, person.id)
    assert len(logged) == 1
    assert logged[0].subject == "Betreff wie gelesen"
    assert logged[0].template_id == template_id


def test_a_failed_send_is_not_logged(db):
    """The log answers "has this gone out"; a refusal must not make it say yes."""
    from app.emailing.graph_client import GraphApiError

    person = _person(db)
    template_id = _template(db, "Willkommen")

    with (
        patch("app.emailing.graph_client.send_email", new=AsyncMock(side_effect=GraphApiError("abgelehnt"))),
        patch("app.emailing.graph_client.get_access_token", new=AsyncMock(return_value="token")),
    ):
        with pytest.raises(GraphApiError):
            asyncio.run(
                send_person_message(
                    db,
                    CONFIG,
                    person=person,
                    subject="s",
                    body="b",
                    occasion=OCCASION_ONBOARDING,
                    step="leg_assigned_at",
                    template_id=template_id,
                )
            )

    assert log_repo.list_for_person(db, person.id) == []


def test_after_a_send_the_button_has_become_a_date(db, no_drafts):
    """The two states the card shows are read from the log, not from a flag."""
    person = _person(db)
    template_id = _template(db, "Willkommen", step="leg_assigned_at")
    tracker = onboarding_repo.start_for_person(db, person.id, registered_at=date(2026, 1, 1))
    tracker.leg_assigned_at = date(2026, 1, 2)
    onboarding_repo.update(db, tracker)
    tracker = onboarding_repo.get_by_person(db, person.id)

    before = due_templates(db, tracker, OCCASION_ONBOARDING)[0]
    assert not before.was_sent and before.sent_on == ""

    log_repo.record(
        db,
        person_id=person.id,
        template_id=template_id,
        occasion=OCCASION_ONBOARDING,
        step="leg_assigned_at",
        subject="s",
        body="b",
        recipient_emails=["muster@example.invalid"],
        attachment_filenames=[],
    )

    after = due_templates(db, tracker, OCCASION_ONBOARDING)[0]
    assert after.was_sent
    assert len(after.sent_on) == 10, "nur das Datum, nicht der Zeitstempel"


# --- The attachment that is produced, not attached -------------------------


def test_the_contract_is_built_when_a_form_is_stored(db, tmp_path):
    """A ticked Gesellschaftsvertrag becomes a real file."""
    from app.pdf.membership_contract import build_contract
    from app.domain.membership_contract import ContractFields

    source = tmp_path / "formular.pdf"
    build_contract(ContractFields(names="Vorlage"), None, source)
    leg_document_repo.put(db, "membership_contract", "formular.pdf", source.read_bytes())
    person = _person(db)

    prepared = message_attachments.prepare(db, person, ["membership_contract"], directory=tmp_path / "out")

    assert len(prepared) == 1
    assert prepared[0].is_ready
    assert prepared[0].path.exists()
    assert prepared[0].filename.endswith(".pdf")


def test_a_missing_form_is_named_and_does_not_block(db, tmp_path):
    """The administrator asked for a warning, not a refusal."""
    person = _person(db)

    prepared = message_attachments.prepare(db, person, ["membership_contract"], directory=tmp_path / "out")

    assert not prepared[0].is_ready
    assert "Kein Formular hinterlegt" in prepared[0].problem
    assert prepared[0].path is None, "nichts Halbes, das vollständig aussieht"


def test_an_invoice_cannot_be_attached_outside_a_billing_run(db, tmp_path):
    """It is sent from the Rechnungslauf page, which knows the run."""
    person = _person(db)

    prepared = message_attachments.prepare(db, person, ["invoice"], directory=tmp_path / "out")

    assert not prepared[0].is_ready
    assert "Rechnungsversand" in prepared[0].problem


# --- The promise of this feature -------------------------------------------


def _seed_person_with_due_welcome() -> int:
    """Seed one onboarding for which the seeded "Willkommen" is due.

    Written through `connection_scope()` on purpose: that is the database the
    page reads, while the `db` fixture hands out a separate in-memory one.
    """
    with connection_scope() as connection:
        person = _person(connection)
        tracker = onboarding_repo.start_for_person(connection, person.id, registered_at=date(2026, 1, 1))
        tracker.leg_assigned_at = date(2026, 1, 2)
        onboarding_repo.update(connection, tracker)
        return person.id


def test_rendering_the_worklist_sends_nothing():
    """The one guarantee: a mail leaves only on a click.

    Driven rather than inspected -- the page is rendered with a due baustein
    on it, which is exactly the situation in which an automatism would fire.
    """
    person_id = _seed_person_with_due_welcome()

    from app.gui.pages import onboardings as onboardings_page

    with patch("app.emailing.graph_client.send_email", new=AsyncMock()) as send:
        client = Client(ui.page("/probe-message-send-nothing")(lambda: None), request=None)
        with client:
            onboardings_page.onboardings_page()

    send.assert_not_awaited()
    with connection_scope() as connection:
        assert log_repo.list_for_person(connection, person_id) == []


def test_the_card_offers_the_send_button():
    """The seeded Willkommen reaches the card, as a button."""
    _seed_person_with_due_welcome()

    from app.gui.pages import onboardings as onboardings_page

    client = Client(ui.page("/probe-message-send-button")(lambda: None), request=None)
    with client:
        onboardings_page.onboardings_page()
        buttons = [
            element.text for element in client.elements.values() if element.__class__.__name__ == "Button"
        ]

    assert any("Willkommen senden" in (text or "") for text in buttons), buttons


def test_the_seeded_drafts_are_there_after_the_migration(db):
    """Migration 55 brings the texts that had nothing to carry over."""
    names = {template.name for template in template_repo.list_all(db)}

    assert {"Willkommen", "Erinnerung Gesellschaftsvertrag", "Bei der BKW angemeldet"} <= names
    assert "Austritt bestätigt" in names

    welcome = next(t for t in template_repo.list_all(db) if t.name == "Willkommen")
    assert welcome.occasion == OCCASION_ONBOARDING
    assert welcome.step == "leg_assigned_at"
    assert welcome.trigger_kind == TRIGGER_STEP_DONE
    assert welcome.auto_attachments == ["membership_contract"]
    assert "{briefanrede}" in welcome.body

    reminder = next(t for t in template_repo.list_all(db) if t.name == "Erinnerung Gesellschaftsvertrag")
    assert reminder.trigger_kind == TRIGGER_STEP_PENDING
    assert reminder.deadline_days == 30


def test_the_seeded_drafts_do_not_displace_the_carried_over_texts(db):
    """Migration 55 fills the table, and the carry-over must still happen.

    `_seed_message_templates` used to ask "is `message_template` empty",
    which the drafts answered -- so on a database where migrations 52 and 55
    run together (restoring an older backup) the administrator's own invoice
    and Mahnung wording was never carried over at all.
    """
    names = {template.name for template in template_repo.list_all(db)}

    assert {"Rechnung", "1. Mahnung", "2. Mahnung"} <= names
    assert {"Willkommen", "Austritt bestätigt"} <= names


def test_no_seeded_draft_names_a_real_person_or_place(db):
    """The repository is public; a draft must carry no member's data."""
    for template in template_repo.list_all(db):
        assert "@" not in template.body or "{" in template.body
        assert "Ittigen" not in template.body, "keine Ortsangabe in einem Entwurf"
