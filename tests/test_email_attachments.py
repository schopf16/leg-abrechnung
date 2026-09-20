"""Tests for sending a broadcast with several attachments.

Written after a real send went out without its attachment and nobody
noticed: nothing on the final step said whether anything was attached.
The wording shown before that irreversible action is therefore tested as
carefully as the payload itself.
"""

import asyncio
import dataclasses
from unittest.mock import AsyncMock, patch

import pytest

from app.config import GraphConfig
from app.emailing import bulk_send, graph_client
from app.emailing.graph_client import MAX_INLINE_ATTACHMENT_BYTES, Attachment, GraphApiError, send_email
from app.gui.pages.email_dispatch import describe_attachments
from app.models import email_log as email_log_repo
from app.models import person as person_repo
from app.models.person import Person

_CONFIG = GraphConfig(
    tenant_id="t", client_id="c", client_secret="s", sender_address="leg@example.ch", sender_name="LEG"
)


def _person(db, name="Muster", email="a@example.ch") -> Person:
    person_id = person_repo.create(
        db,
        Person(
            id=None,
            salutation="",
            company="",
            first_name="Anna",
            last_name=name,
            contact_email=email,
            contact_phone="",
            billing_street="",
            billing_house_number="",
            billing_postal_code="",
            billing_city="",
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


# -- what the administrator is told before sending ---------------------------


def test_no_attachment_is_stated_as_plainly_as_an_attachment():
    """Silence is what caused the incident: an empty list must produce a
    sentence, not an empty string."""
    assert describe_attachments([]) == "Ohne Anhang."


def test_one_attachment_is_named_not_counted():
    text = describe_attachments([("Reglement.pdf", b"x" * 2048)])

    assert "1 Anhang" in text
    assert "Reglement.pdf" in text
    assert "2 KB" in text


def test_several_attachments_are_all_named():
    text = describe_attachments([("a.pdf", b"x" * 1024), ("b.docx", b"y" * 1024)])

    assert "2 Anhängen" in text
    assert "a.pdf" in text
    assert "b.docx" in text


def test_the_stated_total_is_the_sum_not_the_largest():
    """Microsoft's limit is on the message, so the total is the number
    that decides whether a send will be rejected."""
    text = describe_attachments([("a.pdf", b"x" * 1024 * 1024), ("b.pdf", b"y" * 1024 * 1024)])

    assert "2,0 MB" in text


# -- the payload -------------------------------------------------------------


def _send_with(attachments):
    """Run `send_email` against a mocked Graph and return the sent payload."""
    with patch("httpx.AsyncClient") as client_cls:
        response = AsyncMock()
        response.status_code = 202
        client = client_cls.return_value.__aenter__.return_value
        client.post = AsyncMock(return_value=response)
        asyncio.run(
            send_email(
                _CONFIG,
                "token",
                to_address="a@example.ch",
                to_name="Anna",
                subject="Betreff",
                body="Text",
                attachments=attachments,
            )
        )
        return client.post.call_args.kwargs["json"]["message"]


def test_every_attachment_reaches_the_payload_in_order(tmp_path):
    first = tmp_path / "eins.pdf"
    first.write_bytes(b"%PDF-1")
    second = tmp_path / "zwei.txt"
    second.write_text("hallo", encoding="utf-8")

    message = _send_with(
        [
            Attachment(path=first, filename="eins.pdf"),
            Attachment(path=second, filename="zwei.txt"),
        ]
    )

    assert [a["name"] for a in message["attachments"]] == ["eins.pdf", "zwei.txt"]
    assert message["attachments"][0]["contentType"] == "application/pdf"
    assert message["attachments"][1]["contentType"].startswith("text/plain")


def test_no_attachments_means_no_attachments_key(tmp_path):
    """An empty list must not send an empty `attachments` array."""
    assert "attachments" not in _send_with([])


def test_the_size_limit_applies_to_the_total_not_to_each_file(tmp_path):
    """Files that each pass on their own fail together -- checking them
    one by one would have let this through."""
    half = MAX_INLINE_ATTACHMENT_BYTES // 2 + 1024
    paths = []
    for index in range(3):
        path = tmp_path / f"gross{index}.bin"
        path.write_bytes(b"x" * half)
        paths.append(Attachment(path=path, filename=path.name))

    with pytest.raises(GraphApiError) as excinfo:
        _send_with(paths)

    assert "zusammen" in str(excinfo.value)
    for path in paths:
        assert path.filename in str(excinfo.value)


def test_a_single_file_within_the_limit_still_passes(tmp_path):
    path = tmp_path / "knapp.bin"
    path.write_bytes(b"x" * (MAX_INLINE_ATTACHMENT_BYTES - 1024))

    message = _send_with([Attachment(path=path, filename="knapp.bin")])

    assert len(message["attachments"]) == 1


# -- the broadcast -----------------------------------------------------------


def test_every_recipient_gets_every_attachment(db, tmp_path):
    """The same set for the whole batch -- a second recipient losing the
    attachment would be exactly the reported symptom."""
    recipients = [_person(db, "Eins", "eins@example.ch"), _person(db, "Zwei", "zwei@example.ch")]
    first = tmp_path / "a.pdf"
    first.write_bytes(b"%PDF")
    second = tmp_path / "b.pdf"
    second.write_bytes(b"%PDF")
    attachments = [
        Attachment(path=first, filename="a.pdf"),
        Attachment(path=second, filename="b.pdf"),
    ]

    with patch.object(graph_client, "get_access_token", AsyncMock(return_value="token")):
        with patch.object(graph_client, "send_email", AsyncMock()) as send:
            asyncio.run(
                bulk_send.send_broadcast_email(
                    db, _CONFIG, recipients, "Betreff", "Text", scope="all", attachments=attachments
                )
            )

    assert send.await_count == 2
    for call in send.await_args_list:
        assert [a.filename for a in call.kwargs["attachments"]] == ["a.pdf", "b.pdf"]


def test_the_history_records_every_attachment_name(db, tmp_path):
    recipients = [_person(db)]
    path = tmp_path / "Reglement, final.pdf"  # a comma in the name, on purpose
    path.write_bytes(b"%PDF")
    attachments = [
        Attachment(path=path, filename="Reglement, final.pdf"),
        Attachment(path=path, filename="Beilage.pdf"),
    ]

    with patch.object(graph_client, "get_access_token", AsyncMock(return_value="token")):
        with patch.object(graph_client, "send_email", AsyncMock()):
            asyncio.run(
                bulk_send.send_broadcast_email(
                    db, _CONFIG, recipients, "Betreff", "Text", scope="all", attachments=attachments
                )
            )

    stored = email_log_repo.list_all(db)[0].attachment_filenames
    # One per line, so a comma inside a filename cannot split it wrongly.
    assert stored.splitlines() == ["Reglement, final.pdf", "Beilage.pdf"]


def test_a_send_without_attachments_records_none(db):
    recipients = [_person(db)]

    with patch.object(graph_client, "get_access_token", AsyncMock(return_value="token")):
        with patch.object(graph_client, "send_email", AsyncMock()):
            asyncio.run(
                bulk_send.send_broadcast_email(db, _CONFIG, recipients, "Betreff", "Text", scope="all")
            )

    assert email_log_repo.list_all(db)[0].attachment_filenames is None


# -- reading the upload event ------------------------------------------------
#
# This is where the page broke silently: NiceGUI 3.16 replaced
# `event.name` / `event.content.read()` with `event.file.name` /
# `await event.file.read()`. The old call raised AttributeError inside the
# handler, NiceGUI logged and swallowed it, and every broadcast went out
# without its attachment with nothing on screen saying so. The dependency
# bump that introduced it passed CI, because nothing exercised this path.


class _FakeUpload:
    """Stands in for NiceGUI's `FileUpload`: a name and an awaitable read."""

    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    async def read(self) -> bytes:
        return self._content


def test_an_uploaded_file_is_read_into_name_and_content():
    from app.gui.pages.email_dispatch import read_uploaded_file

    result = asyncio.run(read_uploaded_file(_FakeUpload("Reglement.pdf", b"%PDF")))

    assert result == ("Reglement.pdf", b"%PDF")


def test_spaces_and_parentheses_in_a_filename_are_not_a_problem():
    """The name from the real report -- the first suspicion was the spaces,
    which turned out to be innocent."""
    from app.gui.pages.email_dispatch import read_uploaded_file

    name, content = asyncio.run(
        read_uploaded_file(_FakeUpload("Informationsbulletin Nr. 1 (5).pdf", b"x" * 57978))
    )

    assert name == "Informationsbulletin Nr. 1 (5).pdf"
    assert len(content) == 57978


def test_nicegui_still_hands_us_the_event_shape_we_read():
    """A contract test against NiceGUI itself.

    `read_uploaded_file` is called with `UploadEventArguments.file` and
    expects `.name` plus an awaitable `.read()`. If a future NiceGUI
    renames or re-shapes that, this fails loudly here instead of silently
    inside an event handler, which is exactly how the last change escaped.
    """
    import inspect

    from nicegui.elements.upload_files import FileUpload
    from nicegui.events import MultiUploadEventArguments, UploadEventArguments

    single = {f.name for f in dataclasses.fields(UploadEventArguments)}
    multi = {f.name for f in dataclasses.fields(MultiUploadEventArguments)}

    assert "file" in single, f"UploadEventArguments changed: {single}"
    assert "files" in multi, f"MultiUploadEventArguments changed: {multi}"
    assert "name" in {f.name for f in dataclasses.fields(FileUpload)}
    assert inspect.iscoroutinefunction(FileUpload.read), "FileUpload.read is no longer awaitable"


# -- the wiring, not just the helpers ----------------------------------------
#
# The helper tests above would all pass with the widget wired to nothing,
# or wired to a handler that reads the event instead of its files. These
# drive the page's own upload element through NiceGUI's documented
# simulation entry point (`Upload.handle_uploads`).


def _rendered_email_page():
    """Render the E-Mail-Versand page and hand back its upload element.

    Returns:
        `(upload_element, client)`.
    """
    from nicegui import Client, ui
    from nicegui.elements.upload import Upload

    from app.gui.pages import email_dispatch  # noqa: F401

    client = Client(ui.page("/probe-email")(lambda: None), request=None)
    with client:
        email_dispatch.email_dispatch_page()
    uploads = [e for e in client.elements.values() if isinstance(e, Upload)]
    assert len(uploads) == 1, f"expected exactly one ui.upload, found {len(uploads)}"
    return uploads[0], client


async def _upload(upload, files) -> None:
    """Deliver files to the handler the page actually registered.

    Calls the element's registered `on_multi_upload` handler rather than
    going through `Upload.handle_uploads`: without a running NiceGUI app,
    `handle_event` defers the coroutine instead of running it, so nothing
    would happen. This still fails if the widget is not wired, if the
    handler reads the event instead of `event.files`, or if the files
    never reach the page -- which is what these tests are for.

    Args:
        upload: The page's `ui.upload` element.
        files: `FileUpload` instances to deliver.

    Returns:
        None.
    """
    from nicegui.events import MultiUploadEventArguments

    handlers = upload._multi_upload_handlers
    assert handlers, "the upload widget has no on_multi_upload handler wired"
    event = MultiUploadEventArguments(sender=upload, client=upload.client, files=files)
    for handler in handlers:
        await handler(event)


def _file(name: str, content: bytes):
    from nicegui.elements.upload_files import SmallFileUpload

    return SmallFileUpload(name, "application/pdf", content)


def test_an_upload_through_the_page_reaches_the_attachment_list(db):
    """End to end through the real widget: a regression to reading the
    event instead of its files, or an unwired handler, fails here."""
    upload, client = _rendered_email_page()

    with client:
        asyncio.run(_upload(upload, [_file("Reglement.pdf", b"%PDF-1.4")]))

    labels = [e.text for e in client.elements.values() if getattr(e, "text", None)]
    assert any("Reglement.pdf" in text for text in labels), "attachment never reached the page"


def test_selecting_several_files_attaches_all_of_them(db):
    """The assumption behind using `on_multi_upload`: every selected file
    arrives. With a per-file handler that resets the widget, Quasar aborts
    the siblings and only the first survives."""
    upload, client = _rendered_email_page()

    with client:
        asyncio.run(
            _upload(
                upload,
                [
                    _file("eins.pdf", b"%PDF-1"),
                    _file("zwei.pdf", b"%PDF-2"),
                    _file("drei.pdf", b"%PDF-3"),
                ],
            )
        )

    labels = " ".join(e.text for e in client.elements.values() if getattr(e, "text", None))
    for name in ("eins.pdf", "zwei.pdf", "drei.pdf"):
        assert name in labels, f"{name} missing -- siblings lost"


def test_the_widget_is_wired_for_multiple_files_in_one_event(db):
    """NiceGUI only batches the upload when `multiple` *and*
    `on_multi_upload` are set (`Upload.__init__`). Without the batch prop
    Quasar uploads each file in its own parallel request, and any reset
    aborts the ones still in flight -- so this is the property that keeps
    the attachments from silently disappearing again."""
    upload, _ = _rendered_email_page()

    assert upload._props.get("multiple") is True
    assert upload._props.get("batch") is True, "not batched: files would upload in parallel"
