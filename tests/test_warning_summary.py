"""Tests for collapsing repeated findings on the overview."""

import pytest

from app.domain.quality_checks import QualityWarning, summarise_warnings


def _warning(category: str, message: str, link: str = "/x", summary: str = "", summary_link=None):
    """Build a warning."""
    return QualityWarning(
        category=category, message=message, link=link, summary=summary, summary_link=summary_link
    )


def test_several_findings_of_one_kind_become_one_counted_line():
    """The whole point: the overview has to stay readable."""
    warnings = [
        _warning("address_not_official", f"Adresse {n}", summary="Adressen prüfen", summary_link="/persons")
        for n in range(7)
    ]

    collapsed = summarise_warnings(warnings)

    assert [(w.message, w.link) for w in collapsed] == [("7 Adressen prüfen", "/persons")]


def test_a_single_finding_keeps_its_own_message():
    """Naming the one record beats counting to one, and costs the same line."""
    warnings = [
        _warning("leg_not_assigned", "Messpunkt „CH-1“ hat keine LEG.", summary="Messpunkte ohne LEG")
    ]

    assert [w.message for w in summarise_warnings(warnings)] == ["Messpunkt „CH-1“ hat keine LEG."]


def test_a_collapsed_line_points_at_the_list_not_at_one_record():
    """Most per-record warnings link to their own record."""
    warnings = [
        _warning(
            "onboarding_overdue",
            f"Aufnahme {n}",
            link=f"/persons/{n}",
            summary="Aufnahmen hängen",
            summary_link="/onboardings",
        )
        for n in range(3)
    ]

    collapsed = summarise_warnings(warnings)

    assert [(w.message, w.link) for w in collapsed] == [("3 Aufnahmen hängen", "/onboardings")]


def test_one_check_can_still_speak_twice_for_two_pages():
    """Addresses on Standorte and on Personen are two jobs."""
    warnings = [
        _warning("address_not_official", "a", summary="Standort-Adressen prüfen", summary_link="/sites"),
        _warning("address_not_official", "b", summary="Standort-Adressen prüfen", summary_link="/sites"),
        _warning("address_not_official", "c", summary="Personen-Adressen prüfen", summary_link="/persons"),
        _warning("address_not_official", "d", summary="Personen-Adressen prüfen", summary_link="/persons"),
    ]

    collapsed = summarise_warnings(warnings)

    assert [(w.message, w.link) for w in collapsed] == [
        ("2 Standort-Adressen prüfen", "/sites"),
        ("2 Personen-Adressen prüfen", "/persons"),
    ]


def test_a_warning_without_a_summary_is_never_collapsed():
    """An opt-in, so a check that has nothing sensible to count keeps saying it."""
    warnings = [_warning("reading_gap", f"Lücke {n}") for n in range(4)]

    assert len(summarise_warnings(warnings)) == 4


def test_the_order_groups_first_appeared_in_is_kept():
    """The overview's order is deliberate; collapsing must not reshuffle it."""
    warnings = [
        _warning("a", "a1", summary="A"),
        _warning("b", "b1", summary="B"),
        _warning("a", "a2", summary="A"),
        _warning("b", "b2", summary="B"),
    ]

    assert [w.message for w in summarise_warnings(warnings)] == ["2 A", "2 B"]


def test_nothing_in_means_nothing_out():
    """A clean deployment shows no list at all."""
    assert summarise_warnings([]) == []


@pytest.mark.parametrize("count", [2, 3, 10])
def test_the_count_is_the_number_of_findings(count):
    """The figure is what tells the reader how much work it is."""
    warnings = [_warning("x", f"m{n}", summary="Dinge") for n in range(count)]

    assert summarise_warnings(warnings)[0].message == f"{count} Dinge"
