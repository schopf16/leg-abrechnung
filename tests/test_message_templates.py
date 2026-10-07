"""Textbausteine: the store, the carried-over texts, and the page."""

import sqlite3

import pytest
from nicegui import Client, ui

from app.db.connection import connection_scope
from app.db.schema import initialize_database
from app.emailing import graph_client
from app.models import message_template as template_repo
from app.domain import auto_attachments
from app.domain.auto_attachments import KEY_INVOICE, KEY_MEMBERSHIP_CONTRACT
from app.models import leg_document as leg_document_repo
from app.models.message_template import (
    OCCASION_DUNNING1,
    OCCASION_DUNNING2,
    OCCASION_INVOICE,
    OCCASION_OFFBOARDING,
    OCCASION_ONBOARDING,
    TRIGGER_STEP_DONE,
    TRIGGER_STEP_PENDING,
    MessageTemplate,
)


def _template(
    name: str = "Probebaustein",
    *,
    occasion: str = OCCASION_ONBOARDING,
    step: str = "registered_at",
    trigger_kind: str = TRIGGER_STEP_DONE,
    deadline_days: int | None = None,
    subject: str = "Willkommen in der LEG",
    body: str = "{briefanrede}\n\nSchön, dass Sie dabei sind.",
    auto_attachments: list[str] | None = None,
) -> MessageTemplate:
    """Build a template to insert."""
    return MessageTemplate(
        id=None,
        name=name,
        occasion=occasion,
        step=step,
        trigger_kind=trigger_kind,
        deadline_days=deadline_days,
        subject=subject,
        body=body,
        sort_order=100,
        created_at="",
        auto_attachments=list(auto_attachments or []),
    )


# --- The store -------------------------------------------------------------


def test_a_template_survives_a_round_trip(db):
    """Every field comes back as it went in, `None` deadline included."""
    template_id = template_repo.create(db, _template())

    loaded = template_repo.get(db, template_id)

    assert loaded is not None
    assert loaded.name == "Probebaustein"
    assert loaded.occasion == OCCASION_ONBOARDING
    assert loaded.step == "registered_at"
    assert loaded.trigger_kind == TRIGGER_STEP_DONE
    assert loaded.deadline_days is None
    assert loaded.body.startswith("{briefanrede}")
    assert loaded.created_at, "created_at wird beim Einfügen gesetzt"


def test_several_templates_can_share_one_step(db, no_drafts):
    """The whole reason this is a table and not a column pair."""
    template_repo.create(db, _template("Probebaustein", step="contract_signed_at"))
    template_repo.create(
        db,
        _template(
            "Erinnerung Vertrag",
            step="contract_signed_at",
            trigger_kind=TRIGGER_STEP_PENDING,
            deadline_days=30,
        ),
    )

    found = template_repo.list_for_occasion(db, OCCASION_ONBOARDING, "contract_signed_at")

    assert [t.name for t in found] == ["Erinnerung Vertrag", "Probebaustein"]
    assert {t.trigger_kind for t in found} == {TRIGGER_STEP_DONE, TRIGGER_STEP_PENDING}


def test_the_two_processes_do_not_see_each_others_templates(db, no_drafts):
    """An Austritt step and an Aufnahme step can carry the same attribute name, so the occasion has to..."""
    template_repo.create(db, _template("Aufnahme-Text", occasion=OCCASION_ONBOARDING))
    template_repo.create(db, _template("Austritt-Text", occasion=OCCASION_OFFBOARDING, step="decided_at"))

    assert [t.name for t in template_repo.list_for_occasion(db, OCCASION_ONBOARDING)] == ["Aufnahme-Text"]
    assert [t.name for t in template_repo.list_for_occasion(db, OCCASION_OFFBOARDING)] == ["Austritt-Text"]


def test_an_attachment_keeps_its_bytes(db):
    """An uploaded file comes back byte for byte, in upload order."""
    template_id = template_repo.create(db, _template())

    template_repo.add_attachment(db, template_id, "Merkblatt.pdf", b"%PDF-1.7 other")
    template_repo.add_attachment(db, template_id, "Hausordnung.pdf", b"%PDF-1.7 ...")

    attachments = template_repo.list_attachments(db, template_id)

    assert [a.filename for a in attachments] == ["Merkblatt.pdf", "Hausordnung.pdf"]
    assert attachments[0].content == b"%PDF-1.7 other"


def test_a_template_remembers_which_documents_it_attaches(db):
    """The checkboxes, which replaced a per-upload role question."""
    template_id = template_repo.create(db, _template(auto_attachments=[KEY_MEMBERSHIP_CONTRACT]))

    loaded = template_repo.get(db, template_id)

    assert loaded.auto_attachments == [KEY_MEMBERSHIP_CONTRACT]


def test_a_document_this_version_does_not_know_is_kept(db):
    """A database edited by a later version must stay usable here."""
    template_id = template_repo.create(
        db, _template(auto_attachments=[KEY_MEMBERSHIP_CONTRACT, "something_newer"])
    )

    loaded = template_repo.get(db, template_id)

    assert loaded.auto_attachments == [KEY_MEMBERSHIP_CONTRACT, "something_newer"]
    assert auto_attachments.label_for("something_newer") == "something_newer"


def test_a_document_is_only_offered_where_it_can_be_delivered():
    """There is no invoice while somebody is being taken on, and no membership contract to fill in when..."""
    onboarding_keys = [entry.key for entry in auto_attachments.for_occasion(OCCASION_ONBOARDING)]
    invoice_keys = [entry.key for entry in auto_attachments.for_occasion(OCCASION_INVOICE)]

    assert onboarding_keys == [KEY_MEMBERSHIP_CONTRACT]
    assert invoice_keys == [KEY_INVOICE]


def test_the_contract_requires_a_stored_source_file(db):
    """The template is part of the application, independent of database uploads."""
    missing = auto_attachments.missing_sources([KEY_MEMBERSHIP_CONTRACT], set())
    assert [entry.key for entry in missing] == [KEY_MEMBERSHIP_CONTRACT]

    leg_document_repo.put(db, KEY_MEMBERSHIP_CONTRACT, "Vertrag.pdf", b"%PDF")

    assert auto_attachments.missing_sources([KEY_MEMBERSHIP_CONTRACT], leg_document_repo.stored_keys(db)) == []


def test_a_generated_document_needs_no_stored_form():
    """The invoice is produced by the billing run and cannot be uploaded in advance."""
    assert auto_attachments.missing_sources([KEY_INVOICE], set()) == []


def test_uploading_a_form_again_replaces_it(db):
    """How a new edition of the Reglement is taken on."""
    leg_document_repo.put(db, KEY_MEMBERSHIP_CONTRACT, "Vertrag_2025.pdf", b"alt")

    leg_document_repo.put(db, KEY_MEMBERSHIP_CONTRACT, "Vertrag_2026.pdf", b"neu")

    document = leg_document_repo.get(db, KEY_MEMBERSHIP_CONTRACT)
    assert document.filename == "Vertrag_2026.pdf"
    assert document.content == b"neu"
    assert len(leg_document_repo.list_all(db)) == 1


def test_deleting_a_template_takes_its_attachments_with_it(db):
    """`ON DELETE CASCADE`, so no orphaned blob stays in the database."""
    template_id = template_repo.create(db, _template())
    template_repo.add_attachment(db, template_id, "Vertrag.pdf", b"x")

    template_repo.delete(db, template_id)

    assert template_repo.get(db, template_id) is None
    assert template_repo.list_attachments(db, template_id) == []


def test_a_deadline_only_makes_sense_with_a_pending_trigger(db):
    """Stored as given; the meaning is the trigger's."""
    template_id = template_repo.create(
        db,
        _template("Erinnerung", trigger_kind=TRIGGER_STEP_PENDING, deadline_days=30),
    )

    loaded = template_repo.get(db, template_id)

    assert loaded.trigger_kind == TRIGGER_STEP_PENDING
    assert loaded.deadline_days == 30


# --- The three texts that moved ---------------------------------------------


def _fresh_database() -> sqlite3.Connection:
    """A database migrated and seeded from scratch."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_database(connection)
    return connection


def test_a_fresh_database_comes_with_the_three_carried_over_texts():
    """They used to be column pairs on `leg_settings`."""
    connection = _fresh_database()

    # Only the carried-over ones: migration 55 seeds four more, for the
    # occasions that had no stored text anywhere to carry over.
    carried = [
        template
        for template in template_repo.list_all(connection)
        if template.occasion in (OCCASION_INVOICE, OCCASION_DUNNING1, OCCASION_DUNNING2)
    ]

    assert [t.occasion for t in carried] == [
        OCCASION_INVOICE,
        OCCASION_DUNNING1,
        OCCASION_DUNNING2,
    ]
    assert [t.name for t in carried] == ["Rechnung", "1. Mahnung", "2. Mahnung"]


def test_an_existing_text_is_carried_over_word_for_word():
    """The administrator's own wording, not a default."""
    connection = _fresh_database()
    connection.execute("DELETE FROM message_template")
    connection.execute(
        "UPDATE leg_settings SET invoice_email_subject = ?, invoice_email_body = ? WHERE id = 1",
        ("Ihre Abrechnung {quartal}", "Guten Tag\n\nim Anhang Ihre Abrechnung."),
    )
    connection.commit()

    initialize_database(connection)

    invoice = template_repo.list_for_occasion(connection, OCCASION_INVOICE)[0]
    assert invoice.subject == "Ihre Abrechnung {quartal}"
    assert invoice.body == "Guten Tag\n\nim Anhang Ihre Abrechnung."


def test_seeding_does_not_run_twice():
    """Opening the app again must not duplicate the three texts, and must not overwrite one that has..."""
    connection = _fresh_database()
    invoice = template_repo.list_for_occasion(connection, OCCASION_INVOICE)[0]
    invoice.subject = "Von Hand geändert"
    template_repo.update(connection, invoice)
    before = len(template_repo.list_all(connection))

    initialize_database(connection)

    assert len(template_repo.list_all(connection)) == before
    assert template_repo.get(connection, invoice.id).subject == "Von Hand geändert"


def test_a_half_migrated_database_is_not_an_error():
    """Restoring an old backup replays migrations from where it stopped."""
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

    assert (
        connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'message_template'").fetchone() is None
    )


# --- The page ---------------------------------------------------------------


def _page(probe: str) -> Client:
    """Render the Textbausteine list."""
    from app.gui.pages import message_templates as page_module

    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        page_module.message_templates_page()
    return client


def _row(client: Client, name: str) -> dict:
    """One row of the list, by the template's name."""
    return next(row for row in _table(client).rows if row["name"] == name)


def _open_pencil(client: Client, name: str) -> None:
    """Click the pencil on one row, as the list's action slot does."""
    table = _table(client)
    row = next(candidate for candidate in table.rows if candidate["name"] == name)
    with client:
        handler = next(
            listener.handler for listener in table._event_listeners.values() if listener.type == "edit"
        )
        handler(type("Event", (), {"args": row})())


def _table(client: Client):
    """The list's table."""
    return next(element for element in client.elements.values() if element.__class__.__name__ == "Table")


def test_the_list_shows_every_template_with_its_trigger():
    """ "Fällig" is the column that says when a button will appear, which is the only thing a trigger..."""
    with connection_scope() as connection:
        template_repo.create(connection, _template("Probebaustein", step="registered_at"))
        template_repo.create(
            connection,
            _template(
                "Erinnerung Vertrag",
                step="contract_signed_at",
                trigger_kind=TRIGGER_STEP_PENDING,
                deadline_days=30,
            ),
        )

    rows = _table(_page("/probe-templates-list")).rows

    by_name = {row["name"]: row for row in rows}
    assert "Probebaustein" in by_name and "Erinnerung Vertrag" in by_name
    assert "Anmeldung bei uns" in by_name["Probebaustein"]["trigger"]
    assert "30 Tage" in by_name["Erinnerung Vertrag"]["trigger"]


def test_the_list_is_ordered_by_occasion_by_default():
    """The administrator's own reasoning: "ich öffne das weil etwas mit dem gesellschaftsvertrag nicht..."""
    from app.gui.pages.message_templates import SORT_OPTIONS

    assert SORT_OPTIONS[0].key == "occasion"
    assert SORT_OPTIONS[0].label == "Anlass"


def test_the_columns_are_the_ones_the_reader_needs():
    """Pinned like every other list's columns."""
    columns = [column["label"] for column in _table(_page("/probe-templates-columns")).columns]

    assert columns == ["Name", "Anlass", "Fällig", "Betreff", "Anhänge", ""]


def test_the_list_names_the_ticked_documents_before_the_uploaded_ones():
    """The ticked document is what makes the mail what it is; an uploaded leaflet is the afterthought."""
    with connection_scope() as connection:
        template_id = template_repo.create(connection, _template(auto_attachments=[KEY_MEMBERSHIP_CONTRACT]))
        template_repo.add_attachment(connection, template_id, "Merkblatt.pdf", b"%PDF")

    row = _row(_page("/probe-templates-attachment"), "Probebaustein")

    assert row["attachments"].startswith("Gesellschaftsvertrag")
    assert "Merkblatt.pdf" in row["attachments"]


def test_the_pencil_opens_the_dialog_with_the_stored_text():
    """Driven, because a slot that emits an event nobody listens for looks exactly like one that works."""
    with connection_scope() as connection:
        template_repo.create(connection, _template(subject="Willkommen in der LEG"))

    client = _page("/probe-templates-edit")
    _open_pencil(client, "Probebaustein")

    subjects = [
        element.value
        for element in client.elements.values()
        if element.__class__.__name__ == "Input" and element.label == "Betreff"
    ]
    assert "Willkommen in der LEG" in subjects

    uploads = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Upload" and element._props.get("label") == "Datei wählen"
    ]
    assert len(uploads) == 1
    assert uploads[0]._props["max-file-size"] == graph_client.MAX_INLINE_ATTACHMENT_BYTES
    assert uploads[0]._props["max-total-size"] == graph_client.MAX_INLINE_ATTACHMENT_BYTES


@pytest.mark.parametrize(
    "occasion, expect_step_select",
    [(OCCASION_ONBOARDING, True), (OCCASION_INVOICE, False)],
)
def test_the_step_and_trigger_only_appear_where_they_mean_something(occasion, expect_step_select):
    """The invoice text is used when an invoice is sent, which is not something this page decides -- so..."""
    with connection_scope() as connection:
        template_repo.create(
            connection,
            _template(occasion=occasion, step="registered_at" if expect_step_select else ""),
        )

    client = _page(f"/probe-templates-fields-{occasion}")
    _open_pencil(client, "Probebaustein")

    step_select = next(
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Select" and element.label == "Schritt"
    )
    assert step_select.visible is expect_step_select


def test_nothing_on_this_page_can_send_a_mail():
    """The promise of this stage, as a test rather than as an intention."""
    from pathlib import Path

    source = Path("app/gui/pages/message_templates.py").read_text(encoding="utf-8")

    assert "send_email" not in source
    assert "bulk_send" not in source
    assert "GraphConfig" not in source
