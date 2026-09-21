"""Tests for itemising a billing document by site and metering point.

The participant stays one customer with one netted amount -- what these
tests pin down is that the document now says where that amount came
from, and that saying so changed no figure.
"""

from datetime import date

import pytest

from app.domain.billing import compute_billing_items, create_or_replace_billing_run
from app.domain.demo_data import _DEMO_QR_IBAN, SUMMER_QUARTER, create_demo_data
from app.domain.distribution import (
    MeteringPointQuarterResult,
    PersonQuarterResult,
    compute_quarter_distribution,
)
from app.models import assignment as assignment_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models.assignment import Assignment
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.reading import Reading, upsert_readings
from app.models.site import Site
from app.pdf.bill_breakdown import MeteringPointInfo, build_bill_breakdown
from app.pdf.export_service import _load_metering_point_info
from app.pdf.layout import CONTENT_BOTTOM_Y, TABLE_BOTTOM_Y
from app.pdf.person_bill_pdf import _energy_lines, generate_person_bill_pdf

PRICE = 12.0


def _info(
    designation: str,
    label: str,
    site_id: int,
    address: str,
    direction: str = DIRECTION_CONSUMPTION,
) -> MeteringPointInfo:
    """Build one metering point's printing data.

    Args:
        designation: Grid operator metering point id.
        label: The administrator's own name for it, or `""`.
        site_id: Site the metering point sits at.
        address: That site's full address.
        direction: Which section of the bill it is listed under.

    Returns:
        The `MeteringPointInfo`.
    """
    return MeteringPointInfo(
        designation=designation,
        label=label,
        site_id=site_id,
        site_address=address,
        direction=direction,
    )


def _result(totals: dict[int, tuple[float, float]]) -> PersonQuarterResult:
    """Build a person's quarter result from per-metering-point totals.

    Args:
        totals: `{metering_point_id: (consumed_kwh, produced_kwh)}`.

    Returns:
        A `PersonQuarterResult` whose person totals match the breakdown.
    """
    person_result = PersonQuarterResult(person_id=1)
    for metering_point_id, (consumed, produced) in totals.items():
        person_result.by_metering_point[metering_point_id] = MeteringPointQuarterResult(
            metering_point_id=metering_point_id,
            consumed_local_kwh=consumed,
            produced_local_kwh=produced,
        )
        person_result.consumed_local_kwh += consumed
        person_result.produced_local_kwh += produced
    return person_result


def test_metering_point_without_label_prints_only_its_designation():
    """An empty label adds nothing -- no stray separator, no blank suffix."""
    assert _info("CH1018", "", 1, "Weg 1").display_name == "CH1018"
    assert _info("CH1018", "Allgemeinstrom", 1, "Weg 1").display_name == "CH1018 Allgemeinstrom"


def test_breakdown_groups_by_site_and_orders_sites_by_address():
    """Sites come out in the same order the Standorte page lists them.

    "Bahnhofweg" before "Dorfstrasse" alphabetically -- and the order must
    not depend on which metering point happened to be inserted first.
    """
    breakdown = build_bill_breakdown(
        _result({20: (100.0, 0.0), 10: (200.0, 0.0)}),
        {
            20: _info("CH20", "", 2, "Dorfstrasse 4, 3063 Ittigen"),
            10: _info("CH10", "", 1, "Bahnhofweg 11, 3063 Ittigen"),
        },
        PRICE,
    )

    assert [site.address for site in breakdown.sites] == [
        "Bahnhofweg 11, 3063 Ittigen",
        "Dorfstrasse 4, 3063 Ittigen",
    ]
    assert breakdown.has_multiple_sites


def test_breakdown_orders_house_numbers_numerically_like_every_other_list():
    """ "Fischrain 9" before "Fischrain 68" -- as plain text, "68" would win.

    The one address key in `app.sort_keys` is what guarantees an address
    sorts identically here and on every page that lists it.
    """
    breakdown = build_bill_breakdown(
        _result({1: (10.0, 0.0), 2: (10.0, 0.0)}),
        {
            1: _info("CH1", "", 1, "Fischrain 68, 3063 Ittigen"),
            2: _info("CH2", "", 2, "Fischrain 9, 3063 Ittigen"),
        },
        PRICE,
    )

    assert [site.address for site in breakdown.sites] == [
        "Fischrain 9, 3063 Ittigen",
        "Fischrain 68, 3063 Ittigen",
    ]


def test_site_balance_is_consumption_minus_feed_in():
    """A site's balance is the figure a participant allocates internally."""
    breakdown = build_bill_breakdown(
        _result({1: (412.3, 0.0), 2: (0.0, 688.1)}),
        {
            1: _info("CH1", "Haushalt", 7, "Dorfstrasse 4"),
            2: _info("CH2", "PV Dach", 7, "Dorfstrasse 4", DIRECTION_FEED_IN),
        },
        PRICE,
    )

    (site,) = breakdown.sites
    assert site.consumed_kwh == pytest.approx(412.3)
    assert site.produced_kwh == pytest.approx(688.1)
    assert site.balance_chf == pytest.approx((412.3 - 688.1) * PRICE / 100)


def test_a_metering_point_without_shared_energy_is_listed_at_zero():
    """A meter that shared nothing still appears -- with 0.000 kWh.

    Leaving it out would make the recipient guess whether it was
    considered at all. Showing the zero says plainly: we looked, there
    was nothing to share here.
    """
    breakdown = build_bill_breakdown(
        _result({1: (0.0, 0.0), 2: (50.0, 0.0)}),
        {1: _info("CH1", "", 1, "Weg 1"), 2: _info("CH2", "", 1, "Weg 1")},
        PRICE,
    )

    (site,) = breakdown.sites
    assert [row.name for row in site.consumption] == ["CH1", "CH2"]
    silent = next(row for row in site.consumption if row.name == "CH1")
    assert silent.kwh == 0.0
    assert silent.amount_chf == 0.0


def test_a_feed_in_meter_at_zero_stays_on_the_feed_in_side():
    """Which side a meter is listed under follows its direction, not its figures.

    A PV meter that delivered nothing this quarter must not silently
    migrate into the Bezug section, nor disappear.
    """
    breakdown = build_bill_breakdown(
        _result({1: (120.0, 0.0), 2: (0.0, 0.0)}),
        {
            1: _info("CH1", "Haushalt", 1, "Weg 1"),
            2: _info("CH2", "PV Dach", 1, "Weg 1", DIRECTION_FEED_IN),
        },
        PRICE,
    )

    (site,) = breakdown.sites
    assert [row.name for row in site.consumption] == ["CH1 Haushalt"]
    assert [row.name for row in site.feed_in] == ["CH2 PV Dach"]
    assert site.feed_in[0].kwh == 0.0


def test_the_line_up_of_a_bill_is_the_same_in_a_quarter_that_shared_nothing(db):
    """The positions must not differ from one quarter to the next.

    This is the assurance the recipient reads: the same metering points
    in the same order, whatever the quarter happened to produce. Checked
    against the demo data's two real quarters -- summer shares energy,
    winter has no feed-in at all.
    """
    from app.domain.demo_data import WINTER_QUARTER

    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]
    info = _load_metering_point_info(db)

    def line_up(year, quarter, person_id):
        distribution = compute_quarter_distribution(db, leg.id, year, quarter)
        breakdown = build_bill_breakdown(distribution.person_results[person_id], info, PRICE)
        return [
            (site.address, [row.name for row in site.consumption], [row.name for row in site.feed_in])
            for site in breakdown.sites
        ]

    summer = compute_quarter_distribution(db, leg.id, *SUMMER_QUARTER)
    winter = compute_quarter_distribution(db, leg.id, *WINTER_QUARTER)
    assert summer.person_results, "Sommerquartal muss Teilnehmer haben"

    both = set(summer.person_results) & set(winter.person_results)
    assert both, "es muss Personen geben, die in beiden Quartalen teilnehmen"
    for person_id in both:
        assert line_up(*SUMMER_QUARTER, person_id) == line_up(*WINTER_QUARTER, person_id), (
            f"Positionen von Person {person_id} unterscheiden sich zwischen den Quartalen"
        )

    # The one person who legitimately differs: the demo's previous tenant,
    # whose assignment ends mid-August. Someone who has moved out must NOT
    # receive a bill for the following quarter -- the line-up rule holds
    # for participants, not for people who left.
    departed = set(summer.person_results) - set(winter.person_results)
    assert len(departed) == 1
    person = person_repo.get(db, departed.pop())
    assert "Vorgängerin" in person.last_name


def test_breakdown_skips_a_metering_point_whose_master_data_vanished():
    """A bill still renders if a metering point was deleted underneath it."""
    breakdown = build_bill_breakdown(
        _result({1: (50.0, 0.0), 999: (50.0, 0.0)}),
        {1: _info("CH1", "", 1, "Weg 1")},
        PRICE,
    )

    (site,) = breakdown.sites
    assert [row.name for row in site.consumption] == ["CH1"]


def test_single_site_bill_has_no_grand_totals():
    """With one site, a "Total Bezug" line would merely repeat the site's balance."""
    breakdown = build_bill_breakdown(_result({1: (100.0, 0.0)}), {1: _info("CH1", "", 1, "Weg 1")}, PRICE)
    labels = [line.label for line in _energy_lines(breakdown, PRICE)]

    assert labels == ["Weg 1", "Bezug", "CH1", "Saldo Standort"]


def test_multi_site_bill_totals_each_direction_once():
    """Across sites, the reader gets one Bezug and one Einspeisung total."""
    breakdown = build_bill_breakdown(
        _result({1: (100.0, 0.0), 2: (0.0, 40.0)}),
        {1: _info("CH1", "", 1, "Aweg 1"), 2: _info("CH2", "", 2, "Bweg 2", DIRECTION_FEED_IN)},
        PRICE,
    )
    labels = [line.label for line in _energy_lines(breakdown, PRICE)]

    assert labels.count("Total Bezug") == 1
    assert labels.count("Total Einspeisung") == 1
    assert labels.count("Saldo Standort") == 2


def test_multi_site_bill_without_any_feed_in_omits_the_feed_in_total():
    """A participant who only consumes never sees an Einspeisung line."""
    breakdown = build_bill_breakdown(
        _result({1: (100.0, 0.0), 2: (50.0, 0.0)}),
        {1: _info("CH1", "", 1, "Aweg 1"), 2: _info("CH2", "", 2, "Bweg 2")},
        PRICE,
    )
    labels = [line.label for line in _energy_lines(breakdown, PRICE)]

    assert "Total Bezug" in labels
    assert "Total Einspeisung" not in labels


def test_site_balances_add_up_to_the_energy_net(db):
    """The site balances are a finer view of the net, never a different figure.

    Summed, they must equal the item's energy net (its total minus the
    fees) to within the single rounding step `app.domain.billing`
    performs -- the whole point of rounding exactly once.
    """
    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]
    run, items, _, distribution = create_or_replace_billing_run(db, leg.id, *SUMMER_QUARTER)
    info = _load_metering_point_info(db)

    for item in items:
        breakdown = build_bill_breakdown(
            distribution.person_results[item.person_id], info, item.price_rp_per_kwh
        )
        energy_net_rappen = (
            item.net_amount_rappen
            - item.admin_fee_consumption_rappen
            - item.admin_fee_feed_in_rappen
            - item.paper_invoice_rappen
        )
        summed = sum(site.balance_chf for site in breakdown.sites) * 100
        assert abs(summed - energy_net_rappen) < 1, f"Beleg #{item.id} weicht ab"


def test_per_metering_point_totals_sum_to_the_person_totals(db):
    """The distribution's new resolution is the same energy, not extra energy."""
    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]
    distribution = compute_quarter_distribution(db, leg.id, *SUMMER_QUARTER)

    assert distribution.person_results, "demo data must produce shared energy"
    for person_result in distribution.person_results.values():
        consumed = sum(t.consumed_local_kwh for t in person_result.by_metering_point.values())
        produced = sum(t.produced_local_kwh for t in person_result.by_metering_point.values())
        assert consumed == pytest.approx(person_result.consumed_local_kwh, abs=0.001)
        assert produced == pytest.approx(person_result.produced_local_kwh, abs=0.001)


def test_itemising_changed_no_billed_amount(db):
    """The amounts are computed from the person totals, untouched by the breakdown.

    `compute_billing_items` reads `consumed_local_kwh`/`produced_local_kwh`
    only. Feeding it a result stripped of its per-metering-point detail
    must therefore produce byte-identical items -- the guarantee that
    this whole change is presentation.
    """
    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]
    settings = settings_repo.get_settings(db)
    distribution = compute_quarter_distribution(db, leg.id, *SUMMER_QUARTER)
    paper_invoice_by_person = {p.id: p.paper_invoice for p in person_repo.list_all(db)}

    def items_for(dist):
        return compute_billing_items(
            dist,
            settings.price_rp_per_kwh,
            settings.admin_fee_consumption_rp_per_kwh,
            settings.admin_fee_feed_in_rp_per_kwh,
            settings.paper_invoice_rappen,
            paper_invoice_by_person,
        )

    with_detail = items_for(distribution)
    for person_result in distribution.person_results.values():
        person_result.by_metering_point.clear()
    without_detail = items_for(distribution)

    assert [i.net_amount_rappen for i in with_detail] == [i.net_amount_rappen for i in without_detail]
    assert [i.consumed_kwh for i in with_detail] == [i.consumed_kwh for i in without_detail]


def _make_property_management(db, metering_point_count: int):
    """Create one person holding many metering points across two sites.

    Args:
        db: Database connection fixture.
        metering_point_count: How many consumption metering points to create.

    Returns:
        A `(person, leg, metering_point_ids)` tuple.
    """
    # A payable QR-bill needs a configured sender; reuse the demo helper's
    # values rather than inventing a second set.
    settings = settings_repo.get_settings(db)
    settings.address_street = "Sonnenweg 10"
    settings.address_zip = "3000"
    settings.address_city = "Bern"
    settings.address_country = "CH"
    settings.qr_iban = _DEMO_QR_IBAN
    settings_repo.update_settings(db, settings)

    leg_id = leg_repo.create(db, leg_repo.Leg(id=None, name="LEG-Test", note="", created_at=""))
    person_id = person_repo.create(
        db,
        person_repo.Person(
            id=None,
            salutation="",
            company="Verwaltung Muster AG",
            first_name="",
            last_name="",
            contact_email="niemand@example.invalid",
            contact_phone="",
            billing_street="Musterweg",
            billing_house_number="1",
            billing_postal_code="3063",
            billing_city="Ittigen",
            billing_country="CH",
            iban="",
            customer_number=0,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
    )
    site_ids = [
        site_repo.create(
            db,
            Site(
                id=None,
                street=street,
                house_number=number,
                postal_code="3063",
                municipality="Ittigen",
                address_detail="",
                substation_area_id=None,
                created_at="",
            ),
        )
        for street, number in (("Dorfstrasse", "4"), ("Bahnhofweg", "11"))
    ]

    metering_point_ids = []
    for index in range(metering_point_count):
        is_feed_in = index == 0  # one PV meter, so both sections appear
        metering_point_id = metering_point_repo.create(
            db,
            MeteringPoint(
                id=None,
                designation=f"CH1018000000000000000000{index:09d}",
                direction=DIRECTION_FEED_IN if is_feed_in else DIRECTION_CONSUMPTION,
                site_id=site_ids[0] if index < metering_point_count // 2 else site_ids[1],
                leg_id=leg_id,
                pv_capacity_kwp=None,
                battery_capacity_kwh=None,
                created_at="",
                label="PV Dach" if is_feed_in else f"Whg. {index}. OG",
            ),
        )
        assignment_repo.create(
            db,
            Assignment(
                id=None,
                person_id=person_id,
                metering_point_id=metering_point_id,
                valid_from=date(2026, 1, 1),
                valid_to=None,
                created_at="",
            ),
        )
        # One interval is enough: the engine shares min(production,
        # consumption) and attributes each side per metering point.
        upsert_readings(
            db,
            [
                Reading(
                    metering_point_id=metering_point_id,
                    timestamp="2026-07-01T12:00:00",
                    direction=DIRECTION_FEED_IN if is_feed_in else DIRECTION_CONSUMPTION,
                    kwh=100.0 if is_feed_in else 1.0,
                    source="test",
                )
            ],
        )
        metering_point_ids.append(metering_point_id)

    return person_repo.get(db, person_id), leg_repo.get(db, leg_id), metering_point_ids


def test_long_itemisation_paginates_without_touching_the_payment_slip(db, tmp_path):
    """A property management's bill breaks across pages cleanly.

    Regression test for the defect this feature exposed: `layout.py`'s
    table drawing had no page break at all, because no document had ever
    held more than two energy lines. Twenty-six metering points ran
    straight off the bottom of the page and through the area reserved for
    the QR-bill -- silently, since reportlab happily draws outside the
    page.
    """
    person, leg, _ = _make_property_management(db, 26)
    settings = settings_repo.get_settings(db)
    run, items, _, distribution = create_or_replace_billing_run(db, leg.id, 2026, 3)
    (item,) = items
    item.due_date = date.today().isoformat()

    drawn: list[tuple[int, float, str]] = []
    page = [1]
    from reportlab.pdfgen.canvas import Canvas

    original_string, original_right, original_showpage = (
        Canvas.drawString,
        Canvas.drawRightString,
        Canvas.showPage,
    )

    def record(method):
        def wrapper(self, x, y, text, *args, **kwargs):
            if text.strip():
                drawn.append((page[0], y, text))
            return method(self, x, y, text, *args, **kwargs)

        return wrapper

    def record_showpage(self):
        page[0] += 1
        return original_showpage(self)

    Canvas.drawString, Canvas.drawRightString, Canvas.showPage = (
        record(original_string),
        record(original_right),
        record_showpage,
    )
    try:
        generate_person_bill_pdf(
            run,
            item,
            distribution.person_results[person.id],
            person,
            leg,
            settings,
            tmp_path / "verwaltung.pdf",
            metering_point_info=_load_metering_point_info(db),
        )
    finally:
        Canvas.drawString, Canvas.drawRightString, Canvas.showPage = (
            original_string,
            original_right,
            original_showpage,
        )

    # The payment slip is composited in its own coordinate system, so
    # measure only the document's own text, which ends where it begins.
    body = []
    for entry in drawn:
        if entry[2] == "Empfangsschein":
            break
        body.append(entry)

    assert page[0] > 1, "26 Messpunkte müssen umbrechen"
    assert min(y for _, y, _ in body) >= TABLE_BOTTOM_Y, "Text läuft unter den Seitenrand"

    qr_page = max(p for p, _, _ in drawn)
    on_qr_page = [y for p, y, _ in body if p == qr_page]
    assert not on_qr_page or min(on_qr_page) >= CONTENT_BOTTOM_Y, "Belegtext ragt in den Einzahlungsschein"


def test_continuation_page_repeats_the_site_and_section(db, tmp_path):
    """Rows carried onto a new page still say which building they belong to.

    Without this a property management finds half its flats listed under
    no address at all -- exactly the reader the itemisation exists for.
    """
    person, leg, _ = _make_property_management(db, 26)
    settings = settings_repo.get_settings(db)
    run, items, _, distribution = create_or_replace_billing_run(db, leg.id, 2026, 3)
    (item,) = items
    item.due_date = date.today().isoformat()

    drawn: list[str] = []
    from reportlab.pdfgen.canvas import Canvas

    original = Canvas.drawString

    def record(self, x, y, text, *args, **kwargs):
        drawn.append(text)
        return original(self, x, y, text, *args, **kwargs)

    Canvas.drawString = record
    try:
        generate_person_bill_pdf(
            run,
            item,
            distribution.person_results[person.id],
            person,
            leg,
            settings,
            tmp_path / "verwaltung.pdf",
            metering_point_info=_load_metering_point_info(db),
        )
    finally:
        Canvas.drawString = original

    assert any(text.endswith("(Fortsetzung)") and "strasse" in text for text in drawn), (
        "Die Standort-Überschrift muss auf der Folgeseite wiederholt werden"
    )


# --- The click path -----------------------------------------------------
#
# Everything above would still pass if the "Bezeichnung" field were wired
# to nothing at all. This drives the dialog's own save handler, the step
# that "the page renders" never reaches -- the gap that let a broken
# upload handler ship silently for nine days (see CLAUDE.md).


def test_metering_point_form_saves_the_label():
    """Typing a Bezeichnung and pressing Speichern actually persists it.

    Sets its data up through `connection_scope()` rather than the `db`
    fixture, because that is the connection the dialog itself opens --
    the `db` fixture is a separate in-memory database the GUI never sees.
    """
    from nicegui import Client, ui

    from app.db.connection import connection_scope
    from app.gui import metering_point_form

    with connection_scope() as connection:
        site_id = site_repo.create(
            connection,
            Site(
                id=None,
                street="Dorfstrasse",
                house_number="4",
                postal_code="3063",
                municipality="Ittigen",
                address_detail="",
                substation_area_id=None,
                created_at="",
            ),
        )
        metering_point_id = metering_point_repo.create(
            connection,
            MeteringPoint(
                id=None,
                designation="CH1018000000000000000000000000001",
                direction=DIRECTION_CONSUMPTION,
                site_id=site_id,
                leg_id=None,
                pv_capacity_kwp=None,
                battery_capacity_kwh=None,
                created_at="",
                label="",
            ),
        )
        target = metering_point_repo.get(connection, metering_point_id)

    saved: list = []
    client = Client(ui.page("/probe-metering-point")(lambda: None), request=None)
    with client:
        metering_point_form.open_metering_point_form(existing=target, on_saved=saved.append)

    inputs = [
        e
        for e in client.elements.values()
        if e.__class__.__name__ == "Input" and e._props.get("label") == "Bezeichnung (optional)"
    ]
    assert len(inputs) == 1, f"genau ein Bezeichnungsfeld erwartet, gefunden: {len(inputs)}"
    inputs[0].value = "Allgemeinstrom"

    buttons = [
        e
        for e in client.elements.values()
        if e.__class__.__name__ == "Button" and e._props.get("label") == "Speichern"
    ]
    assert len(buttons) == 1, "genau einen Speichern-Knopf erwartet"
    for listener in buttons[0]._event_listeners.values():
        if listener.type == "click":
            listener.handler(None)
            break
    else:
        raise AssertionError("Speichern-Knopf hat keinen Click-Handler")

    assert saved, "on_saved wurde nicht aufgerufen -- der Speichern-Pfad lief nicht durch"
    with connection_scope() as connection:
        assert metering_point_repo.get(connection, metering_point_id).label == "Allgemeinstrom"


def test_a_long_label_is_cut_to_its_column_instead_of_overrunning_the_figures():
    """Free text must not collide with the kWh column.

    "Wohnung 3. Obergeschoss links" beside a 33-character designation is
    already wider than the label column, and reportlab draws past any
    boundary without complaint -- so the column enforces its own.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    from app.pdf.layout import _COL_KWH_X, _LABEL_GUTTER, _LEFT_MARGIN, _LINE_STYLES, _fit

    font, size, indent, _ = _LINE_STYLES["row"]
    available = _COL_KWH_X - _LABEL_GUTTER - _LEFT_MARGIN - indent
    long_name = "CH1018000000000000000000000000001 Wohnung 3. Obergeschoss links, Mieter Muster"

    assert stringWidth(long_name, font, size) > available, "Testfall muss wirklich zu breit sein"
    fitted = _fit(long_name, font, size, available)
    assert stringWidth(fitted, font, size) <= available
    assert fitted.endswith("…")
    assert fitted.startswith("CH1018"), "die Messpunktbezeichnung muss lesbar bleiben"

    short_name = "CH1018 PV"
    assert _fit(short_name, font, size, available) == short_name, "Kurzes bleibt unangetastet"
