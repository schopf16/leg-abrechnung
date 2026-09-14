"""Tests for the shared sorting mechanism (`app.gui.sorting`).

Everything here is a pure function over already-loaded rows, so none of it
needs a rendered NiceGUI page. `render_sort_select` is the one function
this file does not cover: it builds a NiceGUI element, and the project
has no harness that renders a page, so it is verified by hand instead.

The page-level option sets are tested alongside these, because what they
have to guarantee is a property of the whole app rather than of any one
page: every list offers the same control, and -- crucially -- every one
of its options survives being run over a real row. The shared helpers
below are easy to test and were never the risk; a page keying on a dict
field that its row builder does not produce is, and only
`test_every_option_sorts_real_rows_without_raising` catches that.
"""

from dataclasses import dataclass

import pytest

from app.gui.sorting import (
    SortOption,
    address_key,
    apply_sort,
    fold_for_sort,
    number_key,
    person_name_key,
    sort_description,
    text_key,
)


@dataclass
class _FakePerson:
    """Just the name fields `person_name_key` reads."""

    last_name: str = ""
    first_name: str = ""
    company: str = ""


# -- fold_for_sort -----------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Bühler", "buhler"),
        ("Müller", "muller"),
        ("Straßer", "strasser"),
        ("Hervé", "herve"),
        ("  Muster  ", "muster"),
        ("", ""),
        (None, ""),
    ],
)
def test_fold_for_sort(text, expected):
    assert fold_for_sort(text) == expected


def test_umlauts_sort_as_their_base_letter():
    """German rule (DIN 5007 Variant 1): "Bühler" belongs between
    "Buchser" and "Burri".

    Plain code-point ordering (SQLite's BINARY/NOCASE, verified) gives
    `Buchser, Burri, Bühler, Zimmermann, Zwahlen, Zürcher, Ärni` instead:
    a non-leading umlaut slips past its own initial group, and a leading
    one goes behind every "Z..." name.
    """
    names = ["Zimmermann", "Burri", "Bühler", "Buchser", "Ärni", "Zwahlen", "Zürcher"]

    assert sorted(names, key=fold_for_sort) == [
        "Ärni",
        "Buchser",
        "Bühler",
        "Burri",
        "Zimmermann",
        "Zürcher",
        "Zwahlen",
    ]


# -- text_key / number_key ---------------------------------------------------


def test_text_key_compares_part_by_part():
    assert text_key("Muster", "Adrian") < text_key("Muster", "Zoe")
    assert text_key("Muster", "Zoe") < text_key("Näf", "Adrian")


def test_number_key_keeps_missing_values_together_at_the_end():
    values = [3, None, 1, None, 2]

    assert sorted(values, key=number_key) == [1, 2, 3, None, None]


def test_number_key_can_put_missing_values_first():
    values = [3, None, 1]

    assert sorted(values, key=lambda v: number_key(v, missing_last=False)) == [None, 1, 3]


# -- address_key -------------------------------------------------------------


def test_house_numbers_sort_numerically_not_as_text():
    """The user's complaint: "Fischrain 68" must not come before
    "Fischrain 9" just because "6" < "9" as a character."""
    addresses = [("Fischrain", "68"), ("Fischrain", "9"), ("Fischrain", "10"), ("Fischrain", "2")]

    ordered = sorted(addresses, key=lambda a: address_key(*a))

    assert [number for _, number in ordered] == ["2", "9", "10", "68"]


def test_address_key_accepts_a_combined_street_and_number():
    """Pages that only keep the formatted address must get the same order."""
    combined = sorted(["Fischrain 68", "Fischrain 9", "Fischrain 10"], key=address_key)

    assert combined == ["Fischrain 9", "Fischrain 10", "Fischrain 68"]


def test_street_sorts_before_house_number_and_folds_umlauts():
    addresses = [("Zelgweg", "1"), ("Ägertenweg", "99"), ("Fischrain", "68")]

    ordered = sorted(addresses, key=lambda a: address_key(*a))

    assert [street for street, _ in ordered] == ["Ägertenweg", "Fischrain", "Zelgweg"]


def test_house_number_suffix_orders_after_the_bare_number():
    addresses = [("Fischrain", "68b"), ("Fischrain", "68"), ("Fischrain", "68a")]

    ordered = sorted(addresses, key=lambda a: address_key(*a))

    assert [number for _, number in ordered] == ["68", "68a", "68b"]


def test_address_without_a_house_number_sorts_before_the_numbered_ones():
    addresses = [("Fischrain", "2"), ("Fischrain", ""), ("Fischrain", "1")]

    ordered = sorted(addresses, key=lambda a: address_key(*a))

    assert [number for _, number in ordered] == ["", "1", "2"]


def test_address_key_breaks_ties_on_the_extra_parts():
    ordered = sorted(
        [("Dorfstrasse", "1", "3033 Wohlen"), ("Dorfstrasse", "1", "3032 Hinterkappelen")],
        key=lambda a: address_key(*a),
    )

    assert ordered[0][2] == "3032 Hinterkappelen"


# -- person_name_key ---------------------------------------------------------


def test_person_name_key_sorts_by_surname_then_first_name():
    persons = [
        _FakePerson(last_name="Muster", first_name="Zoe"),
        _FakePerson(last_name="Muster", first_name="Adrian"),
        _FakePerson(last_name="Anderegg", first_name="Beat"),
    ]

    ordered = sorted(persons, key=person_name_key)

    assert [p.first_name for p in ordered] == ["Beat", "Adrian", "Zoe"]


def test_company_without_a_contact_person_sorts_under_its_company_name():
    persons = [
        _FakePerson(company="Wyder AG"),
        _FakePerson(last_name="Anderegg"),
    ]

    ordered = sorted(persons, key=person_name_key)

    assert ordered[0].last_name == "Anderegg"


def test_missing_person_sorts_first_instead_of_crashing():
    """A row whose person was deleted must not break the page."""
    assert person_name_key(None) == ("", "")


# -- apply_sort / sort_description -------------------------------------------


_OPTIONS = [
    SortOption("name", "Name", lambda row: text_key(row["name"])),
    SortOption("count", "Anzahl", lambda row: row["count"]),
]


def test_apply_sort_returns_a_new_list_and_leaves_the_input_alone():
    rows = [{"name": "Zulu", "count": 1}, {"name": "Alpha", "count": 2}]

    ordered = apply_sort(rows, _OPTIONS, "name")

    assert [r["name"] for r in ordered] == ["Alpha", "Zulu"]
    assert [r["name"] for r in rows] == ["Zulu", "Alpha"]


@pytest.mark.parametrize("selected", [None, "", "does-not-exist"])
def test_unknown_or_missing_key_falls_back_to_the_first_option(selected):
    """A page must have a defined order even before the select is touched."""
    rows = [{"name": "Zulu", "count": 1}, {"name": "Alpha", "count": 2}]

    assert [r["name"] for r in apply_sort(rows, _OPTIONS, selected)] == ["Alpha", "Zulu"]
    assert sort_description(_OPTIONS, selected) == "nach Name"


def test_sort_description_names_the_selected_option():
    assert sort_description(_OPTIONS, "count") == "nach Anzahl"


# -- the page option sets ----------------------------------------------------


def _page_option_sets() -> list[tuple[str, list[SortOption]]]:
    """Collect every list page's options, so the checks below hold app-wide.

    Returns:
        `(page name, options)` pairs, including the two pages that build
        their options from a person lookup rather than a constant.
    """
    from app.gui.pages import (
        assignments,
        dunning,
        legs,
        metering_points,
        offboardings,
        onboardings,
        persons,
        receivables,
        sites,
        substation_areas,
        web_registrations,
    )

    return [
        ("assignments", assignments.SORT_OPTIONS),
        ("dunning", dunning.SORT_OPTIONS),
        ("legs", legs.SORT_OPTIONS),
        ("legs detail", legs.DETAIL_SORT_OPTIONS),
        ("metering_points", metering_points.SORT_OPTIONS),
        ("offboardings", offboardings.sort_options({})),
        ("onboardings", onboardings.sort_options({})),
        ("persons", persons.SORT_OPTIONS),
        ("receivables", receivables.SORT_OPTIONS),
        ("sites", sites.SORT_OPTIONS),
        ("substation_areas", substation_areas.SORT_OPTIONS),
        ("web_registrations", web_registrations.SORT_OPTIONS),
    ]


@pytest.mark.parametrize("page,options", _page_option_sets(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_list_page_offers_at_least_two_orders(page, options):
    """A page with only one sensible order should show no select at all
    (see `app.gui.pages.signatures`), so anything listed here needs two."""
    assert len(options) >= 2, page


@pytest.mark.parametrize("page,options", _page_option_sets(), ids=lambda v: v if isinstance(v, str) else "")
def test_option_keys_and_labels_are_unique_within_a_page(page, options):
    assert len({o.key for o in options}) == len(options), page
    assert len({o.label for o in options}) == len(options), page


@pytest.mark.parametrize("page,options", _page_option_sets(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_option_produces_a_printable_description(page, options):
    for option in options:
        assert sort_description(options, option.key) == f"nach {option.label}"


def test_the_person_lists_all_default_to_the_surname():
    """The administrator looks people up by name, and gets the same
    default wherever people are listed -- that is the whole point of the
    shared mechanism."""
    from app.gui.pages import offboardings, onboardings, persons, receivables

    for page, options in (
        ("persons", persons.SORT_OPTIONS),
        ("onboardings", onboardings.sort_options({})),
        ("offboardings", offboardings.sort_options({})),
        ("receivables", receivables.SORT_OPTIONS),
    ):
        assert options[0].key == "last_name", page
        assert options[0].label == "Nachname", page


def _representative_rows() -> list[tuple[str, list, list[SortOption]]]:
    """Build two rows per page, in the exact shape that page's own row
    builder produces.

    Each pair deliberately includes the awkward half of the real data --
    a missing person, a `None` customer number, a site that could not be
    resolved, an empty tracker, an unparseable timestamp -- because those
    are what a sort key actually trips over in production.

    Returns:
        `(page name, rows, options)` triples covering every list page.
    """
    from datetime import date

    from app.domain.dunning import DunningCandidate
    from app.gui.pages import (
        assignments,
        dunning,
        legs,
        metering_points,
        offboardings,
        onboardings,
        persons,
        receivables,
        sites,
        substation_areas,
        web_registrations,
    )
    from app.models.person import Person
    from app.models.person_offboarding import STEPS as OFF_STEPS
    from app.models.person_offboarding import PersonOffboarding
    from app.models.person_onboarding import STEPS as ON_STEPS
    from app.models.person_onboarding import PersonOnboarding
    from app.models.web_registration import WebRegistration

    def person(person_id, **overrides) -> Person:
        fields = dict(
            id=person_id,
            salutation="",
            company="",
            first_name="Anna",
            last_name="Muster",
            contact_email="",
            contact_phone="",
            billing_street="Fischrain",
            billing_house_number="68",
            billing_postal_code="3063",
            billing_city="Ittigen",
            billing_country="CH",
            iban="",
            customer_number=7,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="2026-01-01T00:00:00+00:00",
        )
        fields.update(overrides)
        return Person(**fields)

    # A company with no contact person, no customer number, deactivated,
    # and no address at all -- every optional field of the Personen page
    # missing at once.
    sparse_person = person(
        2,
        company="Wyder AG",
        last_name="",
        first_name="",
        customer_number=None,
        active=False,
        billing_street="",
        billing_house_number="",
        billing_postal_code="",
        billing_city="",
    )

    def onboarding(onboarding_id, person_id, *, complete: bool) -> PersonOnboarding:
        tracker = PersonOnboarding(
            id=onboarding_id,
            person_id=person_id,
            registered_at=None,
            leg_assigned_at=None,
            leg_id=None,
            contract_signed_at=None,
            bkw_registered_at=None,
            bkw_confirmed_at=None,
            created_at="2026-01-01T00:00:00+00:00",
        )
        if complete:
            for attr, _ in ON_STEPS:
                setattr(tracker, attr, date(2026, 1, 1))
        return tracker

    def offboarding(offboarding_id, person_id, *, complete: bool, reason: str) -> PersonOffboarding:
        tracker = PersonOffboarding(
            id=offboarding_id,
            person_id=person_id,
            reason=reason,
            decided_at=None,
            metering_point_exit_at=None,
            bkw_informed_at=None,
            person_confirmed_at=None,
            created_at="2026-01-01T00:00:00+00:00",
        )
        if complete:
            for attr, _ in OFF_STEPS:
                setattr(tracker, attr, date(2026, 1, 1))
        return tracker

    def registration(registration_id, **overrides) -> WebRegistration:
        fields = dict(
            id=registration_id,
            cloudflare_id="x",
            company="",
            salutation="",
            first_name="Anna",
            last_name="Muster",
            street="Fischrain",
            house_number="68",
            postal_code="3063",
            city="Ittigen",
            email="",
            phone="",
            bkw_customer_number="",
            iban="",
            message="",
            submitted_at="2026-01-01T10:00:00+00:00",
            imported_at="2026-01-02T00:00:00+00:00",
            person_created=False,
            site_created=False,
            meters=[],
        )
        fields.update(overrides)
        return WebRegistration(**fields)

    # `tracked_persons` is deliberately missing person 99, so every tracker
    # order has to cope with a person deleted out from under it.
    tracked_persons = {1: person(1)}

    return [
        (
            "assignments",
            [
                {
                    "label": "MP1",
                    "rows": [],
                    "designation": "MP1",
                    "street": "Fischrain",
                    "house_number": "12-14",
                    "person_names": ["Muster"],
                    "latest_valid_from": "2026-01-01",
                },
                # No person on this card at all, and no address behind it.
                {
                    "label": "MP2",
                    "rows": [],
                    "designation": "MP2",
                    "street": "",
                    "house_number": "",
                    "person_names": [],
                    "latest_valid_from": "2025-12-31",
                },
            ],
            assignments.SORT_OPTIONS,
        ),
        (
            "dunning",
            [
                DunningCandidate(
                    person=person(1), items=[], item_target_levels={}, level=1, total_open_rappen=5000
                ),
                DunningCandidate(
                    person=sparse_person, items=[], item_target_levels={}, level=2, total_open_rappen=0
                ),
            ],
            dunning.SORT_OPTIONS,
        ),
        (
            "legs",
            [
                {"name": "LEG Ittigen", "metering_points_count": 3, "optimisation_rank": 0},
                # A LEG that was created but never configured.
                {"name": "LEG Bühler", "metering_points_count": 0, "optimisation_rank": 3},
            ],
            legs.SORT_OPTIONS,
        ),
        (
            "legs detail",
            [
                {
                    "designation": "CH100",
                    "direction": "Bezug",
                    "_site_street": "Fischrain",
                    "_site_house_number": "68",
                    "substation_area": "TK-1",
                },
                # The site behind this metering point could not be resolved.
                {
                    "designation": "CH200",
                    "direction": "Einspeisung",
                    "_site_street": "",
                    "_site_house_number": "",
                    "substation_area": "-",
                },
            ],
            legs.DETAIL_SORT_OPTIONS,
        ),
        (
            "metering_points",
            [
                {
                    "designation": "CH100",
                    "site_street": "Fischrain 68",
                    "site_city": "3063 Ittigen",
                    "leg": "LEG Ittigen",
                    "person": "Muster Anna",
                    "direction": "Bezug",
                },
                {
                    "designation": "CH200",
                    "site_street": "?",
                    "site_city": "",
                    "leg": "-",
                    "person": "-",
                    "direction": "Einspeisung",
                },
            ],
            metering_points.SORT_OPTIONS,
        ),
        (
            "offboardings",
            [
                offboarding(1, 1, complete=True, reason="voluntary"),
                # Person unknown, nothing dated, and a reason value that is
                # not in REASON_OPTIONS (an older row, or hand-edited).
                offboarding(2, 99, complete=False, reason="legacy-value"),
            ],
            offboardings.sort_options(tracked_persons),
        ),
        (
            "onboardings",
            [onboarding(1, 1, complete=True), onboarding(2, 99, complete=False)],
            onboardings.sort_options(tracked_persons),
        ),
        ("persons", [person(1), sparse_person], persons.SORT_OPTIONS),
        ("receivables", [(person(1), 1500), (sparse_person, -250)], receivables.SORT_OPTIONS),
        (
            "sites",
            [
                {
                    "plz_municipality": "3063 Ittigen",
                    "substation_area": "TK-1",
                    "_street": "Fischrain",
                    "_house_number": "68",
                    "_postal_code": "3063",
                    "_municipality": "Ittigen",
                },
                # A site saved with nothing filled in but its id.
                {
                    "plz_municipality": "",
                    "substation_area": "-",
                    "_street": "",
                    "_house_number": "",
                    "_postal_code": "",
                    "_municipality": "",
                },
            ],
            sites.SORT_OPTIONS,
        ),
        (
            "substation_areas",
            [
                {"name": "TK-1", "bkw_designation": "BKW-4711", "sites_count": 5},
                {"name": "TK-2", "bkw_designation": None, "sites_count": 0},
            ],
            substation_areas.SORT_OPTIONS,
        ),
        (
            "web_registrations",
            [
                registration(1),
                # Timestamp in a shape `_parse_submitted_date` cannot read,
                # and a company with no contact person.
                registration(
                    2, submitted_at="not a timestamp", company="Wyder AG", last_name="", first_name=""
                ),
            ],
            web_registrations.SORT_OPTIONS,
        ),
    ]


@pytest.mark.parametrize(
    "page,rows,options", _representative_rows(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_every_option_sorts_real_rows_without_raising(page, rows, options):
    """Every option of every page must survive the awkward half of the data.

    The other page-level tests only inspect option metadata, so a key that
    reads a dict field its row builder never produces -- or an attribute
    that was renamed on the model -- would pass them and then crash the
    page the moment someone picks that entry in the select. This is the
    test that fails instead.
    """
    for option in options:
        ordered = apply_sort(rows, options, option.key)
        assert len(ordered) == len(rows), f"{page} / {option.key}"
        assert {id(row) for row in ordered} == {id(row) for row in rows}, f"{page} / {option.key}"

    # The path taken before the select has ever been touched.
    assert len(apply_sort(rows, options, None)) == len(rows), page
