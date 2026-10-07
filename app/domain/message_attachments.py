"""Turning a baustein's ticked documents into files that can go along.

A ticked document is not attached but **produced**: the bundled
Beitrittserklärung is filled from the person's own record. This is where the
ticks become paths, and where a template error is reported to the sender.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from app.domain.auto_attachments import BY_KEY, KEY_INVOICE, KEY_MEMBERSHIP_CONTRACT
from app.domain.membership_contract import gather
from app.models import message_template as message_template_repo
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


#: A file stored on the baustein itself, the same for every recipient.
KEY_STORED = "stored"

#: A document the administrator picks for this one mail, in the send dialog.
KEY_UPLOAD = "upload"


def safe_filename(text: str) -> str:
    """Reduce a name to something Windows accepts in a filename.

    The one copy: a person's name reaches a path here, so this is also what
    keeps a separator or a `..` out of it. Two copies of that would be two
    things to keep right.
    """
    keep = [character if character.isalnum() or character in " -_" else "_" for character in text]
    return "".join(keep).strip() or "Person"


def safe_attachment_filename(name: str) -> str:
    """Reduce a picked file's own name to a safe filename, keeping its extension.

    The extension decides what the recipient's mail client makes of the file,
    so unlike `safe_filename` it survives -- but only a plainly alphanumeric
    one, and the name is taken apart on both separators first: it comes from
    the browser, and it ends up in a path.
    """
    bare = name.replace("\\", "/").rsplit("/", 1)[-1]
    stem, dot, extension = bare.rpartition(".")
    if not dot or not extension.isalnum() or len(extension) > 10:
        return safe_filename(bare)
    return f"{safe_filename(stem) if stem.strip(' .') else 'Dokument'}.{extension.lower()}"


def free_filename(filename: str, taken: Sequence[str]) -> str:
    """Find a name no other attachment of this one mail carries yet."""
    if filename not in taken:
        return filename
    stem, dot, extension = filename.rpartition(".")
    base, suffix = (stem, f".{extension}") if dot else (filename, "")
    for number in range(2, 1000):
        candidate = f"{base} ({number}){suffix}"
        if candidate not in taken:
            return candidate
    return filename


def store_upload(
    content: bytes, name: str, *, directory: Path, taken: Sequence[str] = ()
) -> PreparedAttachment:
    """Write one picked document next to the produced ones, under a free name."""
    filename = free_filename(safe_attachment_filename(name), taken)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    target.write_bytes(content)
    return PreparedAttachment(key=KEY_UPLOAD, label=filename, path=target, filename=filename)


def stored_attachments(
    connection: sqlite3.Connection,
    template_id: Optional[int],
    *,
    directory: Path,
    taken: Sequence[str] = (),
) -> list[PreparedAttachment]:
    """Write out the files stored on one baustein, so they can go along.

    They are the administrator's own documents -- an own contract, a leaflet --
    and they travel with every send of that baustein, where a ticked document
    is produced per person.
    """
    if template_id is None:
        return []
    written: list[PreparedAttachment] = []
    used_names = list(taken)
    for attachment in message_template_repo.list_attachments(connection, template_id):
        filename = free_filename(safe_attachment_filename(attachment.filename), used_names)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        target.write_bytes(attachment.content)
        written.append(PreparedAttachment(key=KEY_STORED, label=filename, path=target, filename=filename))
        used_names.append(filename)
    return written


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
    filename = f"Beitrittserklaerung_{safe_filename(person.display_name)}.pdf"
    target = directory / filename
    try:
        build_contract(gather(connection, person), None, target)
    except ValueError as exc:
        return PreparedAttachment(
            key=KEY_MEMBERSHIP_CONTRACT,
            label=label,
            path=None,
            filename="",
            problem=str(exc),
        )
    return PreparedAttachment(key=KEY_MEMBERSHIP_CONTRACT, label=label, path=target, filename=filename)
