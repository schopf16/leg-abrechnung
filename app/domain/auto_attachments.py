"""The documents a Textbaustein can be told to attach, declared once.

The first version asked "neue Anhänge behandeln als" on every upload, which
the administrator could not make sense of -- and rightly: the question is
not what kind of file this is, it is **which of our documents should go
along**. That is a checkbox per document.

So the documents are a **registry**, and the template stores the keys it
ticked. Adding one later is one entry here plus an upload under
Einstellungen: no migration, no new control, and the dialog looks the same
with two entries as with six. That is the same bargain `app.gui.filter_bar`
makes for filters, and it is why the administrator asked for it in those
words -- *"mache also etwas wie bei den quickfilter das nicht bei jeder
änderung das look & feel anders aussieht"*.

Two kinds of document, and the difference is where the file comes from:

- **A stored form** (`needs_source=True`): the blank Gesellschaftsvertrag,
  uploaded once under Einstellungen because it is a LEG-wide form and not
  something belonging to one text. Ticking it without the form being there
  is a hint in the dialog, not a refusal -- the box may be ticked before
  the file is to hand.
- **A generated document** (`needs_source=False`): the invoice, which the
  billing run produces per person and cannot be uploaded in advance.

`occasions` keeps a document from being offered where it cannot be
delivered: there is no invoice while somebody is being taken on, and no
membership contract to fill in when a quarter is billed.
"""

from dataclasses import dataclass

from app.models.message_template import (
    OCCASION_DUNNING1,
    OCCASION_DUNNING2,
    OCCASION_INVOICE,
    OCCASION_ONBOARDING,
)

#: The filled-in Beitrittserklärung: page 1 from the person's own record,
#: pages 2 onward from the stored form (see `app.pdf.membership_contract`).
KEY_MEMBERSHIP_CONTRACT = "membership_contract"

#: The person's invoice for the billing run being sent.
KEY_INVOICE = "invoice"


@dataclass(frozen=True)
class AutoAttachment:
    """One document a template can be told to attach.

    Attributes:
        key: Persisted value, English like every stored enum since
            migration 43.
        label: The German text on the checkbox.
        hint: One line under it, or `""`.
        needs_source: Whether a file has to be stored under Einstellungen
            before this can be delivered.
        occasions: The `OCCASION_*` values this is offered for.
    """

    key: str
    label: str
    hint: str
    needs_source: bool
    occasions: tuple[str, ...]


#: Every document, in the order the checkboxes appear. Add an entry to offer
#: a new one; nothing else changes.
AUTO_ATTACHMENTS: tuple[AutoAttachment, ...] = (
    AutoAttachment(
        key=KEY_MEMBERSHIP_CONTRACT,
        label="Gesellschaftsvertrag anfügen",
        hint="Seite 1 wird aus den Angaben der Person ausgefüllt",
        needs_source=True,
        occasions=(OCCASION_ONBOARDING,),
    ),
    AutoAttachment(
        key=KEY_INVOICE,
        label="Rechnung anfügen",
        hint="Die Rechnung dieses Abrechnungslaufs",
        needs_source=False,
        occasions=(OCCASION_INVOICE, OCCASION_DUNNING1, OCCASION_DUNNING2),
    ),
)

#: By key, for looking one up without walking the tuple.
BY_KEY = {entry.key: entry for entry in AUTO_ATTACHMENTS}


def for_occasion(occasion: str) -> tuple[AutoAttachment, ...]:
    """The documents that can be attached for one occasion.

    Args:
        occasion: One of `app.models.message_template`'s `OCCASION_*`.

    Returns:
        The applicable entries, in registry order.
    """
    return tuple(entry for entry in AUTO_ATTACHMENTS if occasion in entry.occasions)


def label_for(key: str) -> str:
    """The German label of one key.

    Args:
        key: A registry key, possibly one this version no longer knows.

    Returns:
        Its label, or the key itself -- a template ticked by a later version
        must not make this one unreadable.
    """
    entry = BY_KEY.get(key)
    return entry.label if entry else key


def missing_sources(keys: list[str], stored_keys: set[str]) -> list[AutoAttachment]:
    """Which ticked documents have no file stored yet.

    What the dialog's hint is built from: ticking is allowed before the form
    is to hand, so this is a statement rather than a refusal.

    Args:
        keys: The template's ticked keys.
        stored_keys: Keys that have a file in `leg_document`.

    Returns:
        The entries that need a file and do not have one.
    """
    return [
        entry
        for key in keys
        if (entry := BY_KEY.get(key)) is not None and entry.needs_source and key not in stored_keys
    ]
