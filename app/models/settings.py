"""LEG-wide settings: a single row holding sender data, QR-IBAN and price."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class LegSettings:
    """Settings shared across all LEGs (see `app.models.leg`)."""

    address_street: str
    address_house_number: str
    address_zip: str
    address_city: str
    address_country: str
    qr_iban: str
    price_rp_per_kwh: float
    admin_fee_consumption_rp_per_kwh: float
    admin_fee_feed_in_rp_per_kwh: float
    paper_invoice_rappen: int
    extra_backup_dir: str
    metering_point_country: str
    metering_point_identifier: str
    web_registration_cursor: int
    onboarding_overdue_days: int
    leg_founding_min_persons: int
    production_capacity_warn_percent: float
    invoice_email_subject: str
    invoice_email_body: str
    dunning_new_deadline_days: int
    dunning_minimum_rappen: int
    dunning1_email_subject: str
    dunning1_email_body: str
    dunning2_email_subject: str
    dunning2_email_body: str
    updated_at: str

    @property
    def address_street_with_number(self) -> str:
        """`"Strasse Hausnummer"`, with either part omitted if empty."""
        return " ".join(part for part in (self.address_street, self.address_house_number) if part)

    @staticmethod
    def from_row(row: sqlite3.Row) -> "LegSettings":
        """Build a `LegSettings` instance from a `sqlite3.Row`."""
        return LegSettings(
            address_street=row["address_street"],
            address_house_number=row["address_house_number"],
            address_zip=row["address_zip"],
            address_city=row["address_city"],
            address_country=row["address_country"],
            qr_iban=row["qr_iban"],
            price_rp_per_kwh=row["price_rp_per_kwh"],
            admin_fee_consumption_rp_per_kwh=row["admin_fee_consumption_rp_per_kwh"],
            admin_fee_feed_in_rp_per_kwh=row["admin_fee_feed_in_rp_per_kwh"],
            paper_invoice_rappen=row["paper_invoice_rappen"],
            extra_backup_dir=row["extra_backup_dir"],
            metering_point_country=row["metering_point_country"],
            metering_point_identifier=row["metering_point_identifier"],
            web_registration_cursor=row["web_registration_cursor"],
            onboarding_overdue_days=row["onboarding_overdue_days"],
            leg_founding_min_persons=row["leg_founding_min_persons"],
            production_capacity_warn_percent=row["production_capacity_warn_percent"],
            invoice_email_subject=row["invoice_email_subject"],
            invoice_email_body=row["invoice_email_body"],
            dunning_new_deadline_days=row["dunning_new_deadline_days"],
            dunning_minimum_rappen=row["dunning_minimum_rappen"],
            dunning1_email_subject=row["dunning1_email_subject"],
            dunning1_email_body=row["dunning1_email_body"],
            dunning2_email_subject=row["dunning2_email_subject"],
            dunning2_email_body=row["dunning2_email_body"],
            updated_at=row["updated_at"],
        )


def get_settings(connection: sqlite3.Connection) -> LegSettings:
    """Load the single LEG settings row."""
    row = connection.execute("SELECT * FROM leg_settings WHERE id = 1").fetchone()
    if row is None:
        raise RuntimeError("LEG settings row missing; call initialize_database() first.")
    return LegSettings.from_row(row)


def update_settings(connection: sqlite3.Connection, settings: LegSettings) -> None:
    """Persist updated LEG settings."""
    connection.execute(
        """
        UPDATE leg_settings SET
            address_street = ?, address_house_number = ?,
            address_zip = ?, address_city = ?,
            address_country = ?, qr_iban = ?, price_rp_per_kwh = ?,
            admin_fee_consumption_rp_per_kwh = ?, admin_fee_feed_in_rp_per_kwh = ?,
            paper_invoice_rappen = ?,
            extra_backup_dir = ?, metering_point_country = ?, metering_point_identifier = ?,
            web_registration_cursor = ?, onboarding_overdue_days = ?,
            leg_founding_min_persons = ?, production_capacity_warn_percent = ?,
            invoice_email_subject = ?, invoice_email_body = ?,
            dunning_new_deadline_days = ?, dunning_minimum_rappen = ?,
            dunning1_email_subject = ?, dunning1_email_body = ?,
            dunning2_email_subject = ?, dunning2_email_body = ?, updated_at = ?
        WHERE id = 1
        """,
        (
            settings.address_street,
            settings.address_house_number,
            settings.address_zip,
            settings.address_city,
            settings.address_country,
            settings.qr_iban,
            settings.price_rp_per_kwh,
            settings.admin_fee_consumption_rp_per_kwh,
            settings.admin_fee_feed_in_rp_per_kwh,
            settings.paper_invoice_rappen,
            settings.extra_backup_dir,
            settings.metering_point_country,
            settings.metering_point_identifier,
            settings.web_registration_cursor,
            settings.onboarding_overdue_days,
            settings.leg_founding_min_persons,
            settings.production_capacity_warn_percent,
            settings.invoice_email_subject,
            settings.invoice_email_body,
            settings.dunning_new_deadline_days,
            settings.dunning_minimum_rappen,
            settings.dunning1_email_subject,
            settings.dunning1_email_body,
            settings.dunning2_email_subject,
            settings.dunning2_email_body,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
