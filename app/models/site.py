"""site (connection site): the physical grid connection point a substation area is attached to, and
that groups one or more metering points."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Site:
    """One physical grid connection point (site)."""

    id: Optional[int]
    street: str
    house_number: str
    postal_code: str
    municipality: str
    address_detail: str
    substation_area_id: Optional[int]
    created_at: str
    address_confirmed: str = ""
    locality_confirmed: str = ""
    #: Wohneinheiten at this address, `None` while nobody has counted them.
    dwelling_count: Optional[int] = None

    @property
    def full_address(self) -> str:
        """The full postal address as a single display string."""
        street = " ".join(p for p in (self.street, self.house_number) if p)
        city = " ".join(p for p in (self.postal_code, self.municipality) if p)
        return ", ".join(p for p in (street, city) if p)

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Site":
        """Build a `site` from a `sqlite3.Row`."""
        return Site(
            id=row["id"],
            street=row["street"],
            house_number=row["house_number"],
            postal_code=row["postal_code"],
            municipality=row["municipality"],
            address_detail=row["address_detail"],
            substation_area_id=row["substation_area_id"],
            created_at=row["created_at"],
            address_confirmed=row["address_confirmed"],
            locality_confirmed=row["locality_confirmed"],
            dwelling_count=row["dwelling_count"],
        )


def list_all(connection: sqlite3.Connection) -> list[Site]:
    """List all sites, ordered by street (alphabetically, case-insensitive), then house number..."""
    rows = connection.execute(
        """
        SELECT * FROM site
        ORDER BY street COLLATE NOCASE, CAST(house_number AS INTEGER), house_number COLLATE NOCASE,
                 municipality COLLATE NOCASE
        """
    ).fetchall()
    return [Site.from_row(row) for row in rows]


def find_by_address(
    connection: sqlite3.Connection, street: str, house_number: str, postal_code: str
) -> Optional[Site]:
    """Fetch a site by exact (case-insensitive) address/Hausnummer/PLZ match."""
    row = connection.execute(
        """
        SELECT * FROM site
        WHERE lower(street) = lower(?) AND lower(house_number) = lower(?) AND lower(postal_code) = lower(?)
        """,
        (street, house_number, postal_code),
    ).fetchone()
    return Site.from_row(row) if row else None


def get(connection: sqlite3.Connection, site_id: int) -> Optional[Site]:
    """Fetch a single site by id."""
    row = connection.execute("SELECT * FROM site WHERE id = ?", (site_id,)).fetchone()
    return Site.from_row(row) if row else None


def create(connection: sqlite3.Connection, site: Site) -> int:
    """Insert a new site."""
    cursor = connection.execute(
        """
        INSERT INTO site
            (street, house_number, postal_code, municipality, address_detail, substation_area_id,
             created_at, dwelling_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            site.street,
            site.house_number,
            site.postal_code,
            site.municipality,
            site.address_detail,
            site.substation_area_id,
            datetime.now(timezone.utc).isoformat(),
            site.dwelling_count,
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, site: Site) -> None:
    """Update an existing site's data."""
    if site.id is None:
        raise ValueError("Cannot update a Standort without an id.")
    connection.execute(
        """
        UPDATE site SET
            street = ?, house_number = ?, postal_code = ?, municipality = ?, address_detail = ?,
            substation_area_id = ?, dwelling_count = ?
        WHERE id = ?
        """,
        (
            site.street,
            site.house_number,
            site.postal_code,
            site.municipality,
            site.address_detail,
            site.substation_area_id,
            site.dwelling_count,
            site.id,
        ),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, site_id: int) -> None:
    """Delete a site along with its metering points (cascade)."""
    connection.execute("DELETE FROM site WHERE id = ?", (site_id,))
    connection.commit()


def confirm_address(connection: sqlite3.Connection, site_id: int, value: str) -> None:
    """Record that the street/house number/postal code was waved through."""
    connection.execute("UPDATE site SET address_confirmed = ? WHERE id = ?", (value, site_id))
    connection.commit()


def confirm_locality(connection: sqlite3.Connection, site_id: int, value: str) -> None:
    """Record that the locality was waved through."""
    connection.execute("UPDATE site SET locality_confirmed = ? WHERE id = ?", (value, site_id))
    connection.commit()
