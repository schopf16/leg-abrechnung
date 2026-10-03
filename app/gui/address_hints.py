"""The address hints shown above the Standorte and Personen lists.

One card, one line per address the official register disagrees with, each
with **Ja** and **Nein**. Ja writes the official value, Nein records that
this exact text is intended and the line stays away until the text changes.

The wording is the same for every cause -- a typo, the political
municipality where the postal locality belongs, a PO box -- and carries no
explanation. An explanation of why the app is asking costs a line, gets
skipped, and in a list of hints becomes the noise that teaches you to skip
the list.

Why it sits on the list and not on the detail page: the eye shows and the
pencil changes, and a list is neither -- it is where work gets done. The
Austritte page already removes a person one click from its worklist, and
this follows that. The dashboard only states that something needs attention
and links here.

Writing immediately rather than opening the dialog is the same choice the
Genossenschaft corrections make: it is one field, and it is editable again
at any time.
"""

from typing import Callable

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.address_check import (
    KIND_PERSON,
    KIND_SITE,
    AddressIssue,
    find_address_issues,
)
from app.domain.address_lookup import (
    FIELD_HOUSE_NUMBER,
    FIELD_LOCALITY,
    FIELD_POSTAL_CODE,
    FIELD_STREET,
)
from app.gui.safe_notify import safe_notify
from app.models import person as person_repo
from app.models import site as site_repo


def apply_suggestion(issue: AddressIssue) -> None:
    """Write the register's value into the record ("Ja").

    Args:
        issue: The hint that was accepted.

    Returns:
        None.
    """
    #: Which attribute each finding corrects, per record kind. A mapping
    #: rather than if/else because a finding about the postal code used to
    #: land in the street field here -- the kind of slip an "everything that
    #: is not the locality is the street" branch invites.
    attribute = {
        (KIND_SITE, FIELD_STREET): "street",
        (KIND_SITE, FIELD_HOUSE_NUMBER): "house_number",
        (KIND_SITE, FIELD_POSTAL_CODE): "postal_code",
        (KIND_SITE, FIELD_LOCALITY): "municipality",
        (KIND_PERSON, FIELD_STREET): "billing_street",
        (KIND_PERSON, FIELD_HOUSE_NUMBER): "billing_house_number",
        (KIND_PERSON, FIELD_POSTAL_CODE): "billing_postal_code",
        (KIND_PERSON, FIELD_LOCALITY): "billing_city",
    }.get((issue.kind, issue.finding.field))
    if attribute is None or not issue.finding.suggestion:
        return

    with connection_scope() as connection:
        repo = site_repo if issue.kind == KIND_SITE else person_repo
        record = repo.get(connection, issue.object_id)
        if record is None:
            return
        setattr(record, attribute, issue.finding.suggestion)
        repo.update(connection, record)


def _dismiss(issue: AddressIssue) -> None:
    """Record that this exact text is intended ("Nein").

    Args:
        issue: The hint that was declined.

    Returns:
        None.
    """
    with connection_scope() as connection:
        if issue.kind == KIND_SITE:
            if issue.is_locality:
                site_repo.confirm_locality(connection, issue.object_id, issue.dismiss_value)
            else:
                site_repo.confirm_address(connection, issue.object_id, issue.dismiss_value)
        else:
            if issue.is_locality:
                person_repo.confirm_billing_city(connection, issue.object_id, issue.dismiss_value)
            else:
                person_repo.confirm_billing_address(connection, issue.object_id, issue.dismiss_value)


def render_address_hints(kind: str, on_changed: Callable[[], None]) -> None:
    """Render the hints for one kind of record, or nothing at all.

    Draws no card when there is nothing to say -- including when no register
    has been downloaded, which is deliberately indistinguishable from "all
    addresses are fine" on these pages. The Adressregister page is where the
    absence of a register is stated; a list has no business nagging about it.

    Args:
        kind: `KIND_SITE` or `KIND_PERSON`.
        on_changed: Called after a Ja or Nein so the page reloads its rows.

    Returns:
        None.
    """
    container = ui.column().classes("w-full")

    def refresh() -> None:
        """Redraw the card from the current findings.

        Returns:
            None.
        """
        container.clear()
        with connection_scope() as connection:
            issues = [i for i in find_address_issues(connection) if i.kind == kind]
        if not issues:
            return

        with container, ui.card().classes("w-full"):
            ui.label("Adresshinweise").classes("text-body1 font-bold")
            for issue in issues:
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(issue.label).classes("text-body2 flex-grow")
                    ui.label(issue.question).classes("text-body2 text-warning")
                    if issue.finding.suggestion:
                        ui.button(
                            "Ja",
                            on_click=lambda _=None, chosen=issue: _accept(chosen),
                        ).props("flat dense no-caps")
                    ui.button(
                        "Nein",
                        on_click=lambda _=None, chosen=issue: _decline(chosen),
                    ).props("flat dense no-caps")

    def _accept(issue: AddressIssue) -> None:
        """Apply a suggestion and redraw both the card and the page.

        Args:
            issue: The accepted hint.

        Returns:
            None.
        """
        apply_suggestion(issue)
        safe_notify(f"Übernommen: {issue.finding.suggestion}", type="positive")
        refresh()
        on_changed()

    def _decline(issue: AddressIssue) -> None:
        """Record a dismissal and redraw.

        Args:
            issue: The declined hint.

        Returns:
            None.
        """
        _dismiss(issue)
        refresh()
        on_changed()

    refresh()
