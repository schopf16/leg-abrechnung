"""LEG-wide settings page: sender address, QR-IBAN, price, admin fees
(shared across all LEGs -- see `app.models.leg` for the per-LEG name),
and demo data generation.
"""

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.demo_data import DemoDataAlreadyExists, create_demo_data
from app.gui.navigation import page_frame
from app.models import settings as settings_repo
from app.models.settings import LegSettings


@ui.page("/einstellungen")
def einstellungen_page() -> None:
    """Render the LEG-wide settings page.

    Returns:
        None.
    """
    with page_frame("/einstellungen", "Einstellungen"):
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
            price = ui.number(
                "Interner Strompreis (Rp./kWh)",
                value=current.price_rp_per_kwh,
                min=0,
                step=0.1,
                format="%.2f",
            ).classes("w-full")
            verwaltungsaufwand = ui.number(
                "Verwaltungsaufwand (Rp./kWh, nur auf Bezug)",
                value=current.verwaltungsaufwand_rp_per_kwh,
                min=0,
                step=0.01,
                format="%.4f",
            ).classes("w-full")
            papierrechnung_fee = ui.number(
                "Kosten Papierrechnung (CHF, pro Abrechnung)",
                value=current.papierrechnung_rappen / 100,
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
                if verwaltungsaufwand.value is None or verwaltungsaufwand.value < 0:
                    error_label.text = "Verwaltungsaufwand muss positiv sein."
                    return
                if papierrechnung_fee.value is None or papierrechnung_fee.value < 0:
                    error_label.text = "Kosten Papierrechnung müssen positiv sein."
                    return
                updated = LegSettings(
                    address_street=street.value.strip(),
                    address_zip=zip_code.value.strip(),
                    address_city=city.value.strip(),
                    address_country=country.value.strip() or "CH",
                    qr_iban=qr_iban.value.strip().replace(" ", ""),
                    price_rp_per_kwh=float(price.value),
                    verwaltungsaufwand_rp_per_kwh=float(verwaltungsaufwand.value),
                    papierrechnung_rappen=round(float(papierrechnung_fee.value) * 100),
                    updated_at="",
                )
                with connection_scope() as connection:
                    settings_repo.update_settings(connection, updated)
                error_label.text = ""
                ui.notify("Einstellungen gespeichert.", type="positive")

            ui.button("Speichern", on_click=save).classes("mt-2")

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
                f"{len(summary.messpunkt_ids)} Messpunkte, {summary.reading_count} Messwerte.",
                type="positive",
            )

        ui.button("Demo-Daten erzeugen", on_click=generate_demo_data, color="secondary").classes(
            "mt-2"
        )
