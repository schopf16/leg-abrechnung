"""The documents a Textbaustein can be told to attach, declared once."""

from dataclasses import dataclass

from app.models.message_template import (
    OCCASION_DUNNING1,
    OCCASION_DUNNING2,
    OCCASION_INVOICE,
    OCCASION_ONBOARDING,
)

#: The filled-in Beitrittserklärung, using the interactive PDF bundled with
#: the application (see `app.pdf.membership_contract`).
KEY_MEMBERSHIP_CONTRACT = "membership_contract"

#: The person's invoice for the billing run being sent.
KEY_INVOICE = "invoice"


@dataclass(frozen=True)
class AutoAttachment:
    """One document a template can be told to attach."""

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
        hint="Die Formularvorlage ist in der Software hinterlegt und wird ausgefüllt.",
        needs_source=False,
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
    """The documents that can be attached for one occasion."""
    return tuple(entry for entry in AUTO_ATTACHMENTS if occasion in entry.occasions)


def label_for(key: str) -> str:
    """The German label of one key."""
    entry = BY_KEY.get(key)
    return entry.label if entry else key


def missing_sources(keys: list[str], stored_keys: set[str]) -> list[AutoAttachment]:
    """Which ticked documents have no file stored yet."""
    return [
        entry
        for key in keys
        if (entry := BY_KEY.get(key)) is not None and entry.needs_source and key not in stored_keys
    ]
