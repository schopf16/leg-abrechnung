"""LEG-wide settings page: sender address, QR-IBAN, price, admin fees
(shared across all LEGs -- see `app.models.leg` for the per-LEG name),
MeteringPoint Land/identifier defaults, and demo data generation.
"""

from nicegui import ui

from app.config import ConfigError, get_graph_config
from app.db.connection import connection_scope
from app.domain.demo_data import DemoDataAlreadyExists, create_demo_data
from app.domain.iban_validation import normalize_iban, validate_qr_iban
from app.domain.metering_point_validation import validate_identifier, validate_country
from app.emailing import graph_client
from app.emailing.templates import PERSON_PLACEHOLDERS
from app.gui.navigation import page_frame
from app.models import settings as settings_repo
from app.models.settings import LegSettings

#: Shown as a hint above the invoice email template fields -- Person
#: placeholders plus the invoice-only context ones from
#: `app.emailing.bulk_send._invoice_placeholder_values`.
_INVOICE_PLACEHOLDER_HINT = ", ".join(
    f"{{{name}}}" for name in (*PERSON_PLACEHOLDERS, "leg", "quartal", "jahr", "betrag")
)

#: Shown as a hint above the dunning notice template fields -- Person
#: placeholders plus the dunning notice-only context ones from
#: `app.domain.dunning`.
_DUNNING_PLACEHOLDER_HINT = ", ".join(
    f"{{{name}}}" for name in (*PERSON_PLACEHOLDERS, "betrag", "neue_frist")
)


@ui.page("/settings")
def settings_page() -> None:
    """Render the LEG-wide settings page.

    Returns:
        None.
    """
    with page_frame("/settings", "Stammdaten"):
        with connection_scope() as connection:
            current = settings_repo.get_settings(connection)

        ui.label("Einstellungen").classes("text-lg font-bold")
        ui.label(
            "Diese Angaben gelten für alle LEGs (Absender und "
            "Zahlungsempfänger der QR-Rechnung, interner Strompreis, "
            "Gebühren). Der Name auf der Rechnung wird von der "
            "jeweiligen LEG bezogen -- siehe „LEGs“."
        ).classes("text-body2 text-grey-8")

        with ui.card().classes("w-full max-w-lg"):
            street = ui.input("Strasse", value=current.address_street).classes("w-full")
            with ui.row().classes("w-full gap-2"):
                zip_code = ui.input("PLZ", value=current.address_zip).classes("w-24")
                city = ui.input("Ort", value=current.address_city).classes("flex-grow")
            country = ui.input("Land", value=current.address_country or "CH").classes("w-full")
            qr_iban = ui.input("QR-IBAN", value=current.qr_iban).classes("w-full")
            qr_iban_error = ui.label("").classes("text-negative text-caption")

            def check_qr_iban() -> None:
                """Validate the QR-IBAN once the field loses focus.

                Returns:
                    None.
                """
                qr_iban_error.text = validate_qr_iban(qr_iban.value) or ""

            qr_iban.on("blur", check_qr_iban)
            price = ui.number(
                "Interner Strompreis (Rp./kWh)",
                value=current.price_rp_per_kwh,
                min=0,
                step=0.1,
                format="%.2f",
            ).classes("w-full")
            admin_fee_consumption = ui.number(
                "Verwaltungsaufwand Bezug (Rp./kWh)",
                value=current.admin_fee_consumption_rp_per_kwh,
                min=0,
                step=0.01,
                format="%.4f",
            ).classes("w-full")
            admin_fee_feed_in = ui.number(
                "Verwaltungsaufwand Einspeisung (Rp./kWh)",
                value=current.admin_fee_feed_in_rp_per_kwh,
                min=0,
                step=0.01,
                format="%.4f",
            ).classes("w-full")
            paper_invoice_fee = ui.number(
                "Kosten Papierrechnung (CHF, pro Abrechnung)",
                value=current.paper_invoice_rappen / 100,
                min=0,
                step=0.5,
                format="%.2f",
            ).classes("w-full")
            error_label = ui.label("").classes("text-negative")

            def save() -> None:
                """Validate and persist the LEG-wide settings form.

                Returns:
                    None.
                """
                if price.value is None or price.value < 0:
                    error_label.text = "Preis muss positiv sein."
                    return
                if admin_fee_consumption.value is None or admin_fee_consumption.value < 0:
                    error_label.text = "Verwaltungsaufwand Bezug muss positiv sein."
                    return
                if admin_fee_feed_in.value is None or admin_fee_feed_in.value < 0:
                    error_label.text = "Verwaltungsaufwand Einspeisung muss positiv sein."
                    return
                if paper_invoice_fee.value is None or paper_invoice_fee.value < 0:
                    error_label.text = "Kosten Papierrechnung müssen positiv sein."
                    return
                qr_iban_problem = validate_qr_iban(qr_iban.value)
                if qr_iban_problem:
                    qr_iban_error.text = qr_iban_problem
                    error_label.text = qr_iban_problem
                    return
                updated = LegSettings(
                    address_street=street.value.strip(),
                    address_zip=zip_code.value.strip(),
                    address_city=city.value.strip(),
                    address_country=country.value.strip() or "CH",
                    qr_iban=normalize_iban(qr_iban.value),
                    price_rp_per_kwh=float(price.value),
                    admin_fee_consumption_rp_per_kwh=float(admin_fee_consumption.value),
                    admin_fee_feed_in_rp_per_kwh=float(admin_fee_feed_in.value),
                    paper_invoice_rappen=round(float(paper_invoice_fee.value) * 100),
                    extra_backup_dir=current.extra_backup_dir,
                    metering_point_country=current.metering_point_country,
                    metering_point_identifier=current.metering_point_identifier,
                    web_registration_cursor=current.web_registration_cursor,
                    onboarding_overdue_days=current.onboarding_overdue_days,
                    leg_founding_min_persons=current.leg_founding_min_persons,
                    invoice_email_subject=current.invoice_email_subject,
                    invoice_email_body=current.invoice_email_body,
                    dunning_new_deadline_days=current.dunning_new_deadline_days,
                    dunning_minimum_rappen=current.dunning_minimum_rappen,
                    dunning1_email_subject=current.dunning1_email_subject,
                    dunning1_email_body=current.dunning1_email_body,
                    dunning2_email_subject=current.dunning2_email_subject,
                    dunning2_email_body=current.dunning2_email_body,
                    updated_at="",
                )
                with connection_scope() as connection:
                    settings_repo.update_settings(connection, updated)
                error_label.text = ""
                ui.notify("Einstellungen gespeichert.", type="positive")

            ui.button("Speichern", on_click=save).classes("mt-2")

        ui.separator().classes("my-6")

        ui.label("Messpunkt-Vorgaben").classes("text-lg font-bold")
        ui.label(
            "Land und VSE-Identifikator des Netzbetreibers sind bei allen "
            "Messpunkten dieser LEG identisch. Hier hinterlegt, werden sie "
            "beim Anlegen eines neuen Messpunkts als Vorschlag "
            "eingesetzt (dort weiterhin veränderbar)."
        ).classes("text-body2 text-grey-8")
        with ui.card().classes("w-full max-w-lg"):
            with ui.row().classes("w-full gap-2"):
                metering_point_country = ui.input(
                    "Land", value=current.metering_point_country or "CH"
                ).classes("w-24")
                metering_point_identifier = ui.input(
                    "VSE-Identifikator (11-stellig)",
                    value=current.metering_point_identifier,
                ).classes("flex-grow")
            metering_point_defaults_error = ui.label("").classes("text-negative")

            def save_metering_point_defaults() -> None:
                """Validate and persist the MeteringPoint Land/identifier defaults.

                Returns:
                    None.
                """
                country_value = metering_point_country.value.strip().upper()
                identifier_value = metering_point_identifier.value.strip().upper()
                country_problem = validate_country(country_value)
                if country_problem:
                    metering_point_defaults_error.text = country_problem
                    return
                identifier_problem = validate_identifier(identifier_value)
                if identifier_problem:
                    metering_point_defaults_error.text = identifier_problem
                    return
                with connection_scope() as connection:
                    settings = settings_repo.get_settings(connection)
                    settings.metering_point_country = country_value or "CH"
                    settings.metering_point_identifier = identifier_value
                    settings_repo.update_settings(connection, settings)
                metering_point_defaults_error.text = ""
                ui.notify("Messpunkt-Vorgaben gespeichert.", type="positive")

            ui.button("Speichern", on_click=save_metering_point_defaults).classes("mt-2")

        ui.separator().classes("my-6")

        ui.label("Aufnahmeprozess").classes("text-lg font-bold")
        ui.label(
            "Ab wie vielen Tagen ohne Fortschritt beim aktuellen Schritt "
            "einer Aufnahme (siehe „Aufnahmen“) diese in den Auswertungen "
            "und auf der Übersicht als überfällig gemeldet wird."
        ).classes("text-body2 text-grey-8")
        with ui.card().classes("w-full max-w-lg"):
            onboarding_overdue_days = ui.number(
                "Überfällig nach (Tagen)",
                value=current.onboarding_overdue_days,
                min=1,
                step=1,
                format="%.0f",
            ).classes("w-full")
            onboarding_error = ui.label("").classes("text-negative")

            def save_onboarding_threshold() -> None:
                """Validate and persist the onboarding overdue threshold.

                Returns:
                    None.
                """
                if (
                    onboarding_overdue_days.value is None
                    or onboarding_overdue_days.value < 1
                ):
                    onboarding_error.text = "Muss mindestens 1 Tag sein."
                    return
                with connection_scope() as connection:
                    settings = settings_repo.get_settings(connection)
                    settings.onboarding_overdue_days = int(onboarding_overdue_days.value)
                    settings_repo.update_settings(connection, settings)
                onboarding_error.text = ""
                ui.notify("Aufnahmeprozess-Einstellung gespeichert.", type="positive")

            ui.button("Speichern", on_click=save_onboarding_threshold).classes("mt-2")

        ui.separator().classes("my-6")

        ui.label("LEG-Gründung").classes("text-lg font-bold")
        ui.label(
            "Ein Trafokreis braucht mindestens einen Prosumer und einen "
            "Consumer, um lokal verteilen zu können -- das prüft die App "
            "immer. Zusätzlich muss er insgesamt mindestens so viele "
            "Personen (Prosumer- plus Consumer-Anzahl) haben, wie hier "
            "hinterlegt, damit „Trafokreise“, „LEGs“ und die Übersicht "
            "vorschlagen, ihn aus einer LEG mit mehreren Trafokreisen in "
            "eine eigene, besser rabattierte LEG auszugliedern."
        ).classes("text-body2 text-grey-8")
        with ui.card().classes("w-full max-w-lg"):
            leg_founding_min_persons = ui.number(
                "Mindestanzahl Personen",
                value=current.leg_founding_min_persons,
                min=1,
                step=1,
                format="%.0f",
            ).classes("w-full")
            leg_gruendung_error = ui.label("").classes("text-negative")

            def save_leg_founding_min_persons() -> None:
                """Validate and persist the LEG-founding minimum person count.

                Returns:
                    None.
                """
                if (
                    leg_founding_min_persons.value is None
                    or leg_founding_min_persons.value < 1
                ):
                    leg_gruendung_error.text = "Muss mindestens 1 sein."
                    return
                with connection_scope() as connection:
                    settings = settings_repo.get_settings(connection)
                    settings.leg_founding_min_persons = int(leg_founding_min_persons.value)
                    settings_repo.update_settings(connection, settings)
                leg_gruendung_error.text = ""
                ui.notify("LEG-Gründung-Einstellung gespeichert.", type="positive")

            ui.button("Speichern", on_click=save_leg_founding_min_persons).classes("mt-2")

        ui.separator().classes("my-6")

        ui.label("E-Mail-Versand").classes("text-lg font-bold")
        ui.label(
            "Vorlage für den Rechnungsversand per E-Mail (siehe "
            "„Rechnungslauf“) -- einmal hier hinterlegt, kein erneutes "
            "Eintippen pro Quartal nötig, für einen einzelnen Lauf dort "
            "trotzdem noch anpassbar. Verfügbare Platzhalter: "
            f"{_INVOICE_PLACEHOLDER_HINT}."
        ).classes("text-body2 text-grey-8")
        with ui.card().classes("w-full max-w-lg"):
            invoice_subject = ui.input(
                "Betreff", value=current.invoice_email_subject
            ).classes("w-full")
            invoice_body = ui.textarea(
                "Nachricht", value=current.invoice_email_body
            ).classes("w-full").props("rows=6")
            invoice_email_error = ui.label("").classes("text-negative")

            def save_invoice_email() -> None:
                """Validate and persist the invoice email template.

                Returns:
                    None.
                """
                if not invoice_subject.value.strip():
                    invoice_email_error.text = "Betreff darf nicht leer sein."
                    return
                with connection_scope() as connection:
                    settings = settings_repo.get_settings(connection)
                    settings.invoice_email_subject = invoice_subject.value.strip()
                    settings.invoice_email_body = invoice_body.value
                    settings_repo.update_settings(connection, settings)
                invoice_email_error.text = ""
                ui.notify("E-Mail-Vorlage gespeichert.", type="positive")

            ui.button("Speichern", on_click=save_invoice_email).classes("mt-2")

            ui.separator().classes("my-4")

            connection_test_result = ui.label("").classes("text-caption")

            async def test_graph_connection() -> None:
                """Acquire a Graph API access token without sending anything.

                Verifies the Entra ID app registration/credentials in
                `config.local.json` before the first real bulk send is
                attempted.

                Returns:
                    None.
                """
                connection_test_result.text = "Prüfe Verbindung..."
                connection_test_result.classes(remove="text-negative text-positive")
                try:
                    config = get_graph_config()
                except ConfigError as exc:
                    connection_test_result.text = str(exc)
                    connection_test_result.classes(add="text-negative")
                    return
                try:
                    await graph_client.get_access_token(config)
                except (graph_client.GraphAuthError, graph_client.GraphApiError) as exc:
                    connection_test_result.text = str(exc)
                    connection_test_result.classes(add="text-negative")
                    return
                connection_test_result.text = (
                    f"Verbindung erfolgreich -- Absender: {config.sender_address}"
                )
                connection_test_result.classes(add="text-positive")

            ui.button("Verbindung testen", on_click=test_graph_connection).props("outline")

        ui.separator().classes("my-6")

        ui.label("Mahnwesen").classes("text-lg font-bold")
        ui.label(
            "Zwei Stufen gemäss Reglement: die 1. Mahnung gewährt eine neue "
            "Frist, die 2. Mahnung löst die Ausschluss-Prüfung aus (siehe "
            "„Debitoren“/„Mahnwesen“) -- keine Mahngebühr auf irgendeiner "
            "Stufe. Verfügbare Platzhalter: "
            f"{_DUNNING_PLACEHOLDER_HINT}."
        ).classes("text-body2 text-grey-8")
        with ui.card().classes("w-full max-w-lg"):
            dunning_new_deadline_days = ui.number(
                "Neue Zahlungsfrist nach 1. Mahnung (Tage)",
                value=current.dunning_new_deadline_days, min=1, step=1, format="%.0f",
            ).classes("w-full")
            dunning_minimum = ui.number(
                "Bagatellgrenze (CHF, darunter keine Mahnung)",
                value=current.dunning_minimum_rappen / 100, min=0, step=1, format="%.2f",
            ).classes("w-full")

            ui.label("1. Mahnung").classes("font-bold mt-3")
            dunning1_subject = ui.input("Betreff", value=current.dunning1_email_subject).classes("w-full")
            dunning1_body = ui.textarea("Nachricht", value=current.dunning1_email_body).classes(
                "w-full"
            ).props("rows=6")

            ui.label("2. Mahnung").classes("font-bold mt-3")
            dunning2_subject = ui.input("Betreff", value=current.dunning2_email_subject).classes("w-full")
            dunning2_body = ui.textarea("Nachricht", value=current.dunning2_email_body).classes(
                "w-full"
            ).props("rows=6")

            dunning_error = ui.label("").classes("text-negative")

            def save_dunning() -> None:
                """Validate and persist the dunning settings and templates.

                Returns:
                    None.
                """
                if dunning_new_deadline_days.value is None or dunning_new_deadline_days.value < 1:
                    dunning_error.text = "Neue Zahlungsfrist muss mindestens 1 Tag sein."
                    return
                if dunning_minimum.value is None or dunning_minimum.value < 0:
                    dunning_error.text = "Bagatellgrenze muss positiv sein."
                    return
                if not dunning1_subject.value.strip() or not dunning2_subject.value.strip():
                    dunning_error.text = "Betreff darf nicht leer sein."
                    return
                with connection_scope() as connection:
                    settings = settings_repo.get_settings(connection)
                    settings.dunning_new_deadline_days = int(dunning_new_deadline_days.value)
                    settings.dunning_minimum_rappen = round(dunning_minimum.value * 100)
                    settings.dunning1_email_subject = dunning1_subject.value.strip()
                    settings.dunning1_email_body = dunning1_body.value
                    settings.dunning2_email_subject = dunning2_subject.value.strip()
                    settings.dunning2_email_body = dunning2_body.value
                    settings_repo.update_settings(connection, settings)
                dunning_error.text = ""
                ui.notify("Mahnwesen-Einstellungen gespeichert.", type="positive")

            ui.button("Speichern", on_click=save_dunning).classes("mt-2")

        ui.separator().classes("my-6")

        ui.label("Demo-Daten").classes("text-lg font-bold")
        ui.markdown(
            "Erzeugt eine Beispiel-LEG mit Standorten, fünf "
            "Beispiel-Personen (zwei Prosumer, drei reine Bezüger) mit "
            "Messpunkten und Zuordnungen inkl. eines Umzug-Beispiels, "
            "sowie synthetische 15-Minuten-Messwerte für ein Winter- und "
            "ein Sommer-Quartal 2025 zum Ausprobieren der App."
        ).classes("text-body2")

        def generate_demo_data() -> None:
            """Run the demo data generator and report the outcome via a toast.

            Returns:
                None.
            """
            try:
                with connection_scope() as connection:
                    summary = create_demo_data(connection)
            except DemoDataAlreadyExists as exc:
                ui.notify(str(exc), type="warning")
                return
            ui.notify(
                f"Demo-Daten erzeugt: {len(summary.person_ids)} Personen, "
                f"{len(summary.metering_point_ids)} Messpunkte, {summary.reading_count} Messwerte.",
                type="positive",
            )

        ui.button("Demo-Daten erzeugen", on_click=generate_demo_data, color="secondary").classes(
            "mt-2"
        )
