"""LEG settings page: sender data, QR-IBAN, price, and demo data generation."""

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.demo_data import DemoDataAlreadyExists, create_demo_data
from app.gui.navigation import page_frame
from app.models import settings as settings_repo
from app.models.settings import LegSettings


@ui.page("/einstellungen")
def einstellungen_page() -> None:
    """Render the LEG settings page.

    Returns:
        None.
    """
    with page_frame("/einstellungen", "Einstellungen"):
        with connection_scope() as connection:
            current = settings_repo.get_settings(connection)

        ui.label("LEG-Einstellungen").classes("text-lg font-bold")
        ui.label(
            "Diese Angaben gelten für alle Rechnungen und Gutschriften "
            "(Absender und Zahlungsempfänger der QR-Rechnung)."
        ).classes("text-body2 text-grey-8")

        with ui.card().classes("w-full max-w-lg"):
            name = ui.input("Name / Bezeichnung der LEG", value=current.name).classes("w-full")
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
            error_label = ui.label("").classes("text-negative")

            def save() -> None:
                """Validate and persist the LEG settings form.

                Returns:
                    None.
                """
                if not name.value.strip():
                    error_label.text = "Name darf nicht leer sein."
                    return
                if price.value is None or price.value < 0:
                    error_label.text = "Preis muss positiv sein."
                    return
                updated = LegSettings(
                    name=name.value.strip(),
                    address_street=street.value.strip(),
                    address_zip=zip_code.value.strip(),
                    address_city=city.value.strip(),
                    address_country=country.value.strip() or "CH",
                    qr_iban=qr_iban.value.strip().replace(" ", ""),
                    price_rp_per_kwh=float(price.value),
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
            "Erzeugt vier Beispiel-Teilnehmer (zwei Prosumer, zwei reine "
            "Bezüger) mit Zählern, Zuordnungen inkl. eines Umzug-Beispiels, "
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
                f"Demo-Daten erzeugt: {len(summary.participant_ids)} Teilnehmer, "
                f"{len(summary.meter_ids)} Zähler, {summary.reading_count} Messwerte.",
                type="positive",
            )

        ui.button("Demo-Daten erzeugen", on_click=generate_demo_data, color="secondary").classes(
            "mt-2"
        )
