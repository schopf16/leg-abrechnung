"""Turning a baustein's ticked documents into files that can go along.

A ticked document is not attached but **produced**: the Beitrittserklärung's
first page is drawn from the person's own record. So this is where the ticks
become paths, and where one that cannot be produced says why instead of
failing the send -- the administrator may send the mail without it.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.domain.auto_attachments import BY_KEY, KEY_INVOICE, KEY_MEMBERSHIP_CONTRACT
from app.domain.membership_contract import gather
from app.models import leg_document as leg_document_repo
from app.models.person import Person
from app.pdf.membership_contract import build_contract


@dataclass
class PreparedAttachment:
    """One ticked document, produced or explained."""

    key: str
    label: str
    path: Optional[Path]
    filename: str
    problem: str = ""

    @property
    def is_ready(self) -> bool:
        """Whether this document can actually go along."""
        return self.path is not None


def _safe_filename(text: str) -> str:
    """Reduce a name to something Windows accepts in a filename."""
    keep = [character if character.isalnum() or character in " -_" else "_" for character in text]
    return "".join(keep).strip() or "Person"


def prepare(
    connection: sqlite3.Connection,
    person: Person,
    keys: list[str],
    *,
    directory: Path,
) -> list[PreparedAttachment]:
    """Produce every ticked document for one person, in the ticked order."""
    prepared: list[PreparedAttachment] = []
    for key in keys:
        entry = BY_KEY.get(key)
        label = entry.label if entry else key
        if key == KEY_MEMBERSHIP_CONTRACT:
            prepared.append(_membership_contract(connection, person, label, directory))
        elif key == KEY_INVOICE:
            # The invoice belongs to a billing run, which this send knows
            # nothing about -- it is sent from the Rechnungslauf page.
            prepared.append(
                PreparedAttachment(
                    key=key,
                    label=label,
                    path=None,
                    filename="",
                    problem="Nur beim Rechnungsversand verfügbar.",
                )
            )
        else:
            prepared.append(
                PreparedAttachment(key=key, label=label, path=None, filename="", problem="Unbekannt.")
            )
    return prepared


def _membership_contract(
    connection: sqlite3.Connection, person: Person, label: str, directory: Path
) -> PreparedAttachment:
    """Build the filled-in Beitrittserklärung, or say why there is none."""
    document = leg_document_repo.get(connection, KEY_MEMBERSHIP_CONTRACT)
    if document is None:
        # Page 1 alone is not the Gesellschaftsvertrag, so nothing is
        # produced rather than something that looks complete. The send goes
        # ahead without it if the administrator insists.
        return PreparedAttachment(
            key=KEY_MEMBERSHIP_CONTRACT,
            label=label,
            path=None,
            filename="",
            problem=(
                "Kein Formular hinterlegt (Einstellungen → Allgemein → Aufnahmeprozess → LEG-Dokumente)."
            ),
        )
    filename = f"Beitrittserklaerung_{_safe_filename(person.display_name)}.pdf"
    target = directory / filename
    build_contract(gather(connection, person), document.content, target)
    return PreparedAttachment(key=KEY_MEMBERSHIP_CONTRACT, label=label, path=target, filename=filename)
