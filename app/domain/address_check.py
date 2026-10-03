"""Checks the app's own addresses against the register, minus the dismissed.

Kept apart from `app.domain.address_lookup`, which knows only the register
file: this module is the one that joins it to the members' data, and that is
a different job with a different reason to change.

A dismissal is stored as the **confirmed value**, never as a flag or a date
(migration 51). The hint therefore comes back by itself the moment the text
changes, and `address_signature` is what both sides compare -- defined once
here so the check and the confirm button cannot drift apart. Had this been a
date, dismissing a hint in 2026 would still silence a different wrong
address in 2027, and a tick that no longer holds is worse than no tick.

Persons are checked only when the billing country is Switzerland. Street and
house number *are* checked there, which was a deliberate reversal: a PO box
or a "c/o" line produces one hint that one click retires for good, and that
is a better trade than leaving every ordinary typo in a billing address
unchecked.
"""

import sqlite3
from dataclasses import dataclass

from app.domain.address_lookup import (
    FIELD_LOCALITY,
    AddressFinding,
    open_register,
    verify,
)
from app.models import person as person_repo
from app.models import site as site_repo

#: What `kind` can be. Plain strings, like `QualityWarning.category`.
KIND_SITE = "site"
KIND_PERSON = "person"


def address_signature(street: str, house_number: str, postal_code: str) -> str:
    """Compose the text a street/house-number dismissal is recorded against.

    Args:
        street: Street name.
        house_number: House number.
        postal_code: Postal code.

    Returns:
        A single normalised string. Never shown to anybody -- it only has to
        change whenever any part of the address changes, so that a stored
        dismissal stops applying by itself.
    """
    return "|".join(part.strip() for part in (street, house_number, postal_code))


@dataclass(frozen=True)
class AddressIssue:
    """One address the register disagrees with, and which object it belongs to.

    Attributes:
        kind: `KIND_SITE` or `KIND_PERSON`.
        object_id: Primary key of that site or person.
        label: German description of the object, for the list entry.
        finding: What the register says, with the value as stored.
        dismiss_value: What a "Nein" writes into the confirmation column.
            Separate from `finding.value` on purpose: for a locality the two
            happen to be the same text, but for a street or house number the
            dismissal has to cover the whole address, or correcting the
            house number would silently keep a dismissal meant for the old
            one.
    """

    kind: str
    object_id: int
    label: str
    finding: AddressFinding
    dismiss_value: str

    @property
    def question(self) -> str:
        """The one line the UI shows.

        One wording for every cause -- a typo, a political municipality
        instead of the postal locality, a PO box. No explanation of why the
        app is asking: it costs space, gets skipped, and in a list of hints
        it turns into noise.

        Returns:
            `"Meinten Sie: Worblaufen?"`, or a bare statement when the
            register holds nothing close enough to propose.
        """
        if self.finding.suggestion:
            return f"Meinten Sie: {self.finding.suggestion}?"
        return "Nicht im amtlichen Verzeichnis."

    @property
    def is_locality(self) -> bool:
        """Whether this is about the locality rather than the street.

        Returns:
            `True` for a locality finding, which the UI writes into a
            different column and applies to a different field.
        """
        return self.finding.field == FIELD_LOCALITY


def _collect(
    kind: str,
    object_id: int,
    label: str,
    findings: list[AddressFinding],
    signature: str,
    address_confirmed: str,
    locality_confirmed: str,
) -> list[AddressIssue]:
    """Turn one object's findings into issues, dropping the dismissed ones.

    Args:
        kind: `KIND_SITE` or `KIND_PERSON`.
        object_id: Its primary key.
        label: Its German description.
        findings: What `verify` returned.
        signature: `address_signature` of this object's address.
        address_confirmed: Stored street/house-number confirmation.
        locality_confirmed: Stored locality confirmation.

    Returns:
        The undismissed issues.
    """
    issues: list[AddressIssue] = []
    for finding in findings:
        if finding.field == FIELD_LOCALITY:
            dismiss_value = finding.value
            already = bool(locality_confirmed) and locality_confirmed == dismiss_value
        else:
            dismiss_value = signature
            already = bool(address_confirmed) and address_confirmed == dismiss_value
        if already:
            continue
        issues.append(AddressIssue(kind, object_id, label, finding, dismiss_value))
    return issues


def find_address_issues(connection: sqlite3.Connection) -> list[AddressIssue]:
    """Check every site and every Swiss billing address against the register.

    Args:
        connection: Open connection to the application database.

    Returns:
        One `AddressIssue` per undismissed finding, sites first. Empty when
        no register is installed -- an absent register is not evidence
        against anybody's address.
    """
    register = open_register()
    if register is None:
        return []
    try:
        issues: list[AddressIssue] = []
        for site in site_repo.list_all(connection):
            issues.extend(
                _collect(
                    KIND_SITE,
                    site.id,
                    site.full_address,
                    verify(
                        site.street,
                        site.house_number,
                        site.postal_code,
                        site.municipality,
                        connection=register,
                    ),
                    address_signature(site.street, site.house_number, site.postal_code),
                    site.address_confirmed,
                    site.locality_confirmed,
                )
            )

        for person in person_repo.list_all(connection):
            country = (person.billing_country or "CH").strip().upper()
            if country not in ("", "CH"):
                continue
            issues.extend(
                _collect(
                    KIND_PERSON,
                    person.id,
                    person.display_name,
                    verify(
                        person.billing_street,
                        person.billing_house_number,
                        person.billing_postal_code,
                        person.billing_city,
                        connection=register,
                    ),
                    address_signature(
                        person.billing_street,
                        person.billing_house_number,
                        person.billing_postal_code,
                    ),
                    person.billing_address_confirmed,
                    person.billing_city_confirmed,
                )
            )
        return issues
    finally:
        register.close()
