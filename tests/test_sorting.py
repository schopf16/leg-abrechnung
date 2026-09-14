"""Tests for the shared sorting mechanism (`app.gui.sorting`).

Everything here is a pure function over already-loaded rows, so none of it
needs a rendered NiceGUI page. `render_sort_select` is the one exception
and is left to the GUI smoke test.

The page-level option sets are tested alongside these, because what they
have to guarantee is a property of the whole app rather than of any one
page: every list offers the same control, with the same label, and one of
its options selected by default.
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
    "Buchser" and "Burri", not after "Zimmermann" where plain code-point
    ordering puts it."""
    names = ["Zimmermann", "Burri", "Bühler", "Buchser", "Ärni"]

    assert sorted(names, key=fold_for_sort) == ["Ärni", "Buchser", "Bühler", "Burri", "Zimmermann"]


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
    assert sort_description(_OPTIONS, selected) == "sortiert nach Name"


def test_sort_description_names_the_selected_option():
    assert sort_description(_OPTIONS, "count") == "sortiert nach Anzahl"


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
        assert sort_description(options, option.key) == f"sortiert nach {option.label}"


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
