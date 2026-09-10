"""LEG-wide settings: a single row holding sender data, QR-IBAN and price."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class LegSettings:
    """Settings shared across all LEGs (see `app.models.leg`).

    Every LEG bills under its own name (see `Leg.name`), but the sender
    address, QR-IBAN, energy price and admin fees below are the same for
    every LEG in this deployment.

    Attributes:
        address_street: Street and house number of the sender address.
        address_zip: Postal code of the sender address.
        address_city: City of the sender address.
        address_country: ISO-3166 alpha-2 country code, e.g. ``"CH"``.
        qr_iban: QR-IBAN used as the creditor account on invoices.
        price_rp_per_kwh: Internal energy price in Rappen per kWh.
        admin_fee_consumption_rp_per_kwh: Administrative surcharge in
            Rappen per kWh, charged on top of the energy price for a
            person's locally-sourced consumption ("consumption"). Independent
            from `admin_fee_feed_in_rp_per_kwh` -- either can
            be zero while the other is not. Changing this only affects
            billing runs created afterwards: `app.domain.billing` freezes
            the rate actually used onto each `BillingRunItem` at creation
            time, so an already-billed fee never changes retroactively.
        admin_fee_feed_in_rp_per_kwh: The same kind of
            administrative surcharge, charged on a person's
            locally-delivered production ("feed-in") instead.
        paper_invoice_rappen: Flat fee in Rappen charged to persons with
            `Person.paper_invoice` set (paper invoice by post).
        extra_backup_dir: Optional second directory every backup is also
            copied into (e.g. a network drive), in addition to the fixed
            `backups/` folder -- see `app.backup.backup_service`. Empty
            string means no extra copy is made. Stays as set until
            explicitly changed; if the path is unreachable when a backup
            runs (e.g. while travelling), that copy is simply skipped
            with a warning, the primary backup in `backups/` is
            unaffected.
        metering_point_country: Default 2-letter country code for new metering points's
            metering point designation (see `app.domain.metering_point_validation`)
            -- always the same grid operator's country for a single LEG
            deployment, e.g. `"CH"`.
        metering_point_identifier: Default 11-character VSE grid-operator
            identifier for new metering points -- also always the same across
            a single LEG deployment (one grid operator), so storing it
            here saves re-entering it for every MeteringPoint. Editable per
            MeteringPoint regardless.
        web_registration_cursor: The highest Cloudflare submission id
            already fetched from the leg-ittigen.ch registration API --
            see `app.importers.registration_sync`. Managed exclusively by
            that sync, never edited through the settings form.
        onboarding_overdue_days: Number of days a person's current
            onboarding step (see `app.models.person_onboarding`) may stay
            open before it is flagged as overdue in the quality checks.
        leg_founding_min_persons: Minimum `app.domain.participant_mix.
            ParticipantMix.total_persons` (Prosumer- plus Consumer-count)
            a substation area must reach, in addition to already having both a
            Prosumer and a Consumer, before the app suggests splitting it
            off its current multi-substation-area LEG into its own, better-
            discounted one. Default 7.
        invoice_email_subject: Subject template for invoice emails (see
            `app.emailing.bulk_send.send_invoice_emails`), may contain
            `{placeholder}`s (see `app.emailing.templates`). Written once
            in the settings, reused for every billing run instead of
            retyping it each quarter.
        invoice_email_body: Body template for invoice emails, same
            placeholder support.
        dunning_new_deadline_days: Number of days the 1. dunning notice's new
            deadline grants, and the wait before an unpaid item at stage
            1 becomes eligible for the 2. dunning notice (see `app.domain.
            dunning`).
        dunning_minimum_rappen: A person's total open balance must
            be at least this amount for a dunning notice to be raised at all --
            avoids chasing a negligible remainder.
        dunning1_email_subject: Subject template for the 1. dunning notice
            (grants a new deadline; does not yet threaten exclusion --
            wait, it does, see the LEG's own Reglement: a missed new
            deadline leads to membership termination). May contain
            `{placeholder}`s (person placeholders plus `{betrag}`,
            `{new_deadline}` -- see `app.domain.dunning`).
        dunning1_email_body: Body template for the 1. dunning notice.
        dunning2_email_subject: Subject template for the 2. dunning notice
            (sent when the 1. dunning notice's new deadline was missed --
            triggers an exclusion review, never automatic, see
            `app.models.person_offboarding`).
        dunning2_email_body: Body template for the 2. dunning notice.
        updated_at: ISO-8601 timestamp of the last update.
    """

    address_street: str
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
    invoice_email_subject: str
    invoice_email_body: str
    dunning_new_deadline_days: int
    dunning_minimum_rappen: int
    dunning1_email_subject: str
    dunning1_email_body: str
    dunning2_email_subject: str
    dunning2_email_body: str
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
            address_street=row["address_street"],
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
            address_street = ?, address_zip = ?, address_city = ?,
            address_country = ?, qr_iban = ?, price_rp_per_kwh = ?,
            admin_fee_consumption_rp_per_kwh = ?, admin_fee_feed_in_rp_per_kwh = ?,
            paper_invoice_rappen = ?,
            extra_backup_dir = ?, metering_point_country = ?, metering_point_identifier = ?,
            web_registration_cursor = ?, onboarding_overdue_days = ?,
            leg_founding_min_persons = ?,
            invoice_email_subject = ?, invoice_email_body = ?,
            dunning_new_deadline_days = ?, dunning_minimum_rappen = ?,
            dunning1_email_subject = ?, dunning1_email_body = ?,
            dunning2_email_subject = ?, dunning2_email_body = ?, updated_at = ?
        WHERE id = 1
        """,
        (
            settings.address_street,
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
