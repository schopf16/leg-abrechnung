"""LEG-wide settings: a single row holding sender data, QR-IBAN and price."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class LegSettings:
    """The one and only settings record for the local energy community.

    Attributes:
        name: Display name of the LEG, used as payment recipient.
        address_street: Street and house number of the sender address.
        address_zip: Postal code of the sender address.
        address_city: City of the sender address.
        address_country: ISO-3166 alpha-2 country code, e.g. ``"CH"``.
        qr_iban: QR-IBAN used as the creditor account on invoices.
        price_rp_per_kwh: Internal energy price in Rappen per kWh.
        updated_at: ISO-8601 timestamp of the last update.
    """

    name: str
    address_street: str
    address_zip: str
    address_city: str
    address_country: str
    qr_iban: str
    price_rp_per_kwh: float
    updated_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "LegSettings":
        """Build a `LegSettings` instance from a `sqlite3.Row`.

        Args:
            row: Row selected from the `leg_settings` table.

        Returns:
            The corresponding `LegSettings` dataclass instance.
        """
        return LegSettings(
            name=row["name"],
            address_street=row["address_street"],
            address_zip=row["address_zip"],
            address_city=row["address_city"],
            address_country=row["address_country"],
            qr_iban=row["qr_iban"],
            price_rp_per_kwh=row["price_rp_per_kwh"],
            updated_at=row["updated_at"],
        )


def get_settings(connection: sqlite3.Connection) -> LegSettings:
    """Load the single LEG settings row.

    Args:
        connection: Open SQLite connection with an initialized schema.

    Returns:
        The current `LegSettings`.

    Raises:
        RuntimeError: If the settings row is missing (schema not
            initialized via `app.db.schema.initialize_database`).
    """
    row = connection.execute("SELECT * FROM leg_settings WHERE id = 1").fetchone()
    if row is None:
        raise RuntimeError(
            "LEG settings row missing; call initialize_database() first."
        )
    return LegSettings.from_row(row)


def update_settings(connection: sqlite3.Connection, settings: LegSettings) -> None:
    """Persist updated LEG settings.

    Args:
        connection: Open SQLite connection.
        settings: New settings values to store (``updated_at`` is
            overwritten with the current time).

    Returns:
        None.
    """
    connection.execute(
        """
        UPDATE leg_settings SET
            name = ?, address_street = ?, address_zip = ?, address_city = ?,
            address_country = ?, qr_iban = ?, price_rp_per_kwh = ?, updated_at = ?
        WHERE id = 1
        """,
        (
            settings.name,
            settings.address_street,
            settings.address_zip,
            settings.address_city,
            settings.address_country,
            settings.qr_iban,
            settings.price_rp_per_kwh,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
