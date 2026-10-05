"""Checks the app's own addresses against the register, minus the dismissed."""

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
    """Compose the text a street/house-number dismissal is recorded against."""
    return "|".join(part.strip() for part in (street, house_number, postal_code))


@dataclass(frozen=True)
class AddressIssue:
    """One address the register disagrees with, and which object it belongs to."""

    kind: str
    object_id: int
    label: str
    finding: AddressFinding
    dismiss_value: str

    @property
    def question(self) -> str:
        """The one line the UI shows."""
        if self.finding.suggestion:
            return f"Meinten Sie: {self.finding.suggestion}?"
        return "Nicht im amtlichen Verzeichnis."

    @property
    def is_locality(self) -> bool:
        """Whether this is about the locality rather than the street."""
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
    """Turn one object's findings into issues, dropping the dismissed ones."""
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
    """Check every site and every Swiss billing address against the register."""
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


def issue_ids(connection: sqlite3.Connection, kind: str) -> set[int]:
    """Which records of one kind have an open address finding."""
    return {issue.object_id for issue in find_address_issues(connection) if issue.kind == kind}
