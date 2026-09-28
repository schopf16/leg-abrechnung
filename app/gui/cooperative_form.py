"""Shared Genossenschaft membership editor and read-only history.

Two renderers over `app.models.cooperative_membership`, deliberately split
along what the two icons in the Personen list mean: the eye shows, the
pencil changes. Joining, leaving and buying shares are changes, so they
live in the Person edit dialog (`app.gui.person_form`); the detail page
only displays the history (`app.gui.pages.persons`).

Why a history at all, rather than two fields on `person`: a cooperative has
to be able to say who held how many shares on a given day, years later.
So a share purchase does not overwrite a number -- it ends the running
period and opens a new one, and `render_cooperative_editor` does exactly
that from three plain controls, without making the administrator think
about periods.
"""

from datetime import date, timedelta
from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.safe_notify import safe_notify
from app.models import cooperative_membership as cooperative_membership_repo
from app.models.cooperative_membership import CooperativeMembership


def _period_text(membership: CooperativeMembership) -> str:
    """One membership period as a single readable line.

    Args:
        membership: The period to describe.

    Returns:
        E.g. `"01.01.2024 - 30.06.2026: 12 Anteil(e)"`.
    """
    end = membership.valid_to.strftime("%d.%m.%Y") if membership.valid_to else "läuft"
    return f"{membership.valid_from.strftime('%d.%m.%Y')} - {end}: {membership.shares} Anteil(e)"


def render_cooperative_history(person_id: int) -> None:
    """Show one person's membership history, read-only.

    For the detail page, which is what the eye icon opens. No buttons: a
    view does not change anything -- see the module docstring.

    Args:
        person_id: The person whose history to show.

    Returns:
        None.
    """
    with connection_scope() as connection:
        memberships = cooperative_membership_repo.list_for_person(connection, person_id)
        warnings = cooperative_membership_repo.find_warnings(connection, person_id)

    current = next((m for m in memberships if m.covers(date.today())), None)
    if current is None:
        ui.label("Zurzeit kein Genossenschaftsmitglied.").classes("text-grey-6")
    else:
        ui.label(f"Mitglied mit {current.shares} Anteil(en).").classes("text-positive")
    for warning in warnings:
        ui.label(f"⚠ {warning.message}").classes("text-negative text-caption")
    for membership in memberships:
        ui.label(_period_text(membership)).classes("text-body2")
    if memberships:
        ui.label(
            "Ein- und Austritt sowie Änderungen der Anteile werden beim "
            "Bearbeiten der Person erfasst (Stift-Symbol)."
        ).classes("text-caption text-grey-6")


class CooperativeEditor:
    """The editable Genossenschaft controls inside the Person edit dialog.

    Three plain controls -- member yes/no, how many shares, from when --
    which `apply()` turns into the right period bookkeeping: opening a
    period, closing one, or closing one and opening the next so the
    previous share count stays answerable.

    A correction on the very day a period started overwrites that period
    instead of opening a zero-length one, because that is what it is: a
    typo being fixed, not a change of holding.

    Attributes:
        person_id: The person being edited, or `None` while creating one --
            a membership cannot hang on a person who does not exist yet.
    """

    def __init__(self, person_id: Optional[int]) -> None:
        """Render the controls for one person.

        Args:
            person_id: The person being edited, or `None` when creating.

        Returns:
            None.
        """
        self.person_id = person_id
        self._current: Optional[CooperativeMembership] = None
        self._history_column: Optional[ui.column] = None

        if person_id is None:
            ui.label(
                "Nach dem Speichern der Person hier erfassbar -- eine "
                "Mitgliedschaft braucht die Person, an der sie hängt."
            ).classes("text-caption text-grey-6")
            self.is_member = None
            self.shares = None
            self.effective_from = None
            return

        with connection_scope() as connection:
            self._current = cooperative_membership_repo.current_for_person(connection, person_id)

        self.is_member = ui.checkbox("Genossenschaftsmitglied", value=self._current is not None)
        with ui.row().classes("w-full gap-2"):
            self.shares = ui.number(
                "Anzahl Anteile",
                value=self._current.shares if self._current else 0,
                min=0,
                step=1,
                format="%.0f",
            ).classes("w-40")
            self.effective_from = (
                ui.input("Änderung gültig ab", value=date.today().isoformat())
                .props("type=date")
                .classes("flex-grow")
            )
        self.shares.bind_visibility_from(self.is_member, "value")
        ui.label(
            "Eine Änderung beendet den laufenden Zeitraum und eröffnet ab "
            "diesem Datum einen neuen -- die frühere Anzahl bleibt damit "
            "belegt. Beim Austritt ist das Datum der letzte Tag der "
            "Mitgliedschaft."
        ).classes("text-caption text-grey-6")

        self._history_column = ui.column().classes("w-full gap-0")
        self._render_history()

    def _render_history(self) -> None:
        """(Re-)render the period list with its correction buttons.

        Returns:
            None.
        """
        if self._history_column is None:
            return
        with connection_scope() as connection:
            memberships = cooperative_membership_repo.list_for_person(connection, self.person_id)
        self._history_column.clear()
        with self._history_column:
            if not memberships:
                return
            ui.label("Bisheriger Verlauf").classes("text-caption text-grey-6 mt-2")
            for membership in memberships:
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(_period_text(membership)).classes("text-body2 flex-grow")
                    # Correcting a wrong past entry belongs behind the
                    # pencil as much as a new one does, so it is here and
                    # not on the view page.
                    ui.button(
                        icon="edit",
                        on_click=lambda m=membership: self._open_period_dialog(m),
                    ).props("dense flat size=sm").tooltip("Zeitraum korrigieren")
                    ui.button(
                        icon="delete",
                        on_click=lambda m=membership: self._open_delete_dialog(m),
                    ).props("dense flat size=sm color=negative").tooltip("Zeitraum löschen")

    def _open_period_dialog(self, membership: CooperativeMembership) -> None:
        """Correct one recorded period outright.

        For a mistyped date or share count -- not for an ordinary change,
        which the three controls above handle by opening a new period.

        Args:
            membership: The period to correct.

        Returns:
            None.
        """
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
            ui.label("Zeitraum korrigieren").classes("text-lg font-bold")
            ui.label(
                "Ändert den Eintrag selbst. Für einen echten Ein-/Austritt "
                "oder Anteilskauf stattdessen oben das Datum und die Anzahl "
                "setzen -- dann bleibt der bisherige Stand erhalten."
            ).classes("text-caption text-grey-6")
            valid_from = (
                ui.input("Mitglied ab", value=membership.valid_from.isoformat())
                .props("type=date")
                .classes("w-full")
            )
            valid_to = (
                ui.input(
                    "Mitglied bis (leer = läuft)",
                    value=membership.valid_to.isoformat() if membership.valid_to else "",
                )
                .props("type=date")
                .classes("w-full")
            )
            shares = ui.number(
                "Anzahl Anteile", value=membership.shares, min=0, step=1, format="%.0f"
            ).classes("w-full")
            error_label = ui.label("").classes("text-negative")

            def save() -> None:
                """Validate and persist the correction.

                Returns:
                    None.
                """
                try:
                    start = date.fromisoformat(valid_from.value)
                    end = date.fromisoformat(valid_to.value) if valid_to.value else None
                except (TypeError, ValueError):
                    error_label.text = "Ungültiges Datum."
                    return
                if end is not None and end < start:
                    error_label.text = "„Mitglied bis“ liegt vor „Mitglied ab“."
                    return
                membership.valid_from = start
                membership.valid_to = end
                membership.shares = int(shares.value or 0)
                with connection_scope() as connection:
                    cooperative_membership_repo.update(connection, membership)
                    warnings = cooperative_membership_repo.find_warnings(connection, self.person_id)
                dialog.close()
                for warning in warnings:
                    safe_notify(warning.message, type="warning")
                self._render_history()

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Abbrechen", on_click=dialog.close).props("flat")
                ui.button("Übernehmen", on_click=save)
        dialog.open()

    def _open_delete_dialog(self, membership: CooperativeMembership) -> None:
        """Remove one recorded period after confirmation.

        Args:
            membership: The period to delete.

        Returns:
            None.
        """
        with ui.dialog() as confirm, ui.card():
            ui.label("Diesen Zeitraum wirklich aus dem Verlauf löschen?")
            ui.label(
                "Für einen Austritt besser oben das Häkchen entfernen -- dann "
                "bleibt belegt, dass die Mitgliedschaft bestand."
            ).classes("text-caption text-grey-7")
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Abbrechen", on_click=confirm.close).props("flat")

                def do_delete() -> None:
                    with connection_scope() as connection:
                        cooperative_membership_repo.delete(connection, membership.id)
                    confirm.close()
                    self._render_history()

                ui.button("Löschen", on_click=do_delete, color="negative")
        confirm.open()

    def validate(self) -> Optional[str]:
        """Check the controls before the surrounding form saves.

        Returns:
            A German error message, or `None` if the input is usable.
        """
        if self.is_member is None:
            return None
        try:
            effective = date.fromisoformat(self.effective_from.value)
        except (TypeError, ValueError):
            return "Genossenschaft: „Änderung gültig ab“ ist kein gültiges Datum."
        if self._current is not None and effective < self._current.valid_from:
            return (
                "Genossenschaft: das Datum liegt vor dem Beginn der laufenden "
                f"Mitgliedschaft ({self._current.valid_from.strftime('%d.%m.%Y')})."
            )
        return None

    def apply(self, person_id: int, on_warning: Optional[Callable[[str], None]] = None) -> None:
        """Turn the controls into the right period bookkeeping.

        Args:
            person_id: The person the membership belongs to.
            on_warning: Called with each overlap warning the change
                produced, so the caller can surface it. Optional.

        Returns:
            None.
        """
        if self.is_member is None:
            return

        effective = date.fromisoformat(self.effective_from.value)
        wants_member = bool(self.is_member.value)
        shares = int(self.shares.value or 0)
        current = self._current

        if not wants_member:
            if current is not None:
                # The date is the last day of membership, so the period
                # ends on it rather than the day before.
                current.valid_to = effective
                with connection_scope() as connection:
                    cooperative_membership_repo.update(connection, current)
            return

        if current is None:
            with connection_scope() as connection:
                cooperative_membership_repo.create(
                    connection,
                    CooperativeMembership(
                        id=None,
                        person_id=person_id,
                        shares=shares,
                        valid_from=effective,
                        valid_to=None,
                        created_at="",
                    ),
                )
        elif current.shares != shares:
            with connection_scope() as connection:
                if effective == current.valid_from:
                    # Same day it started: a correction, not a change.
                    current.shares = shares
                    cooperative_membership_repo.update(connection, current)
                else:
                    current.valid_to = effective - timedelta(days=1)
                    cooperative_membership_repo.update(connection, current)
                    cooperative_membership_repo.create(
                        connection,
                        CooperativeMembership(
                            id=None,
                            person_id=person_id,
                            shares=shares,
                            valid_from=effective,
                            valid_to=None,
                            created_at="",
                        ),
                    )

        if on_warning:
            with connection_scope() as connection:
                for warning in cooperative_membership_repo.find_warnings(connection, person_id):
                    on_warning(warning.message)
