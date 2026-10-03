"""Tests for narrowing while typing, and for checking a finished address.

Two operations, two sets of tests, because they are not the same question.
Typing wants *narrowing* -- fewer candidates per keystroke, no scoring.
Checking wants *similarity* -- a wrong string is already there and the
useful answer is "did you mean Worblaufen?".

The locality rule is pinned hard here: the app offers the **postal**
locality and never the political municipality, because that is the name the
member reads on the invoice, and because the municipality is not even
determined by the postal code -- 3048 Worblaufen lies in both Ittigen and
Bern. A later session reading `COM_NAME` and thinking it looks more
official would break exactly these tests.
"""

import pytest

from app.domain.address_lookup import (
    FIELD_HOUSE_NUMBER,
    FIELD_LOCALITY,
    FIELD_POSTAL_CODE,
    FIELD_STREET,
    register_available,
    split_query,
    suggest_addresses,
    suggest_localities,
    verify,
)


def _labels(suggestions) -> list[str]:
    """The display lines of a suggestion list.

    Args:
        suggestions: What a suggest function returned.

    Returns:
        One label per suggestion.
    """
    return [suggestion.label for suggestion in suggestions]


# --- Narrowing while typing -----------------------------------------------


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Erstweg 4", ("Erstweg", "4")),
        ("Erstweg", ("Erstweg", "")),
        ("Im langen Loh 19", ("Im langen Loh", "19")),
        ("Erstweg 31.1", ("Erstweg", "31.1")),
        ("Erstweg 8a", ("Erstweg", "8a")),
        ("", ("", "")),
    ],
)
def test_a_trailing_number_is_read_as_the_house_number(query, expected):
    """So "Erstweg 4" narrows to one address while "Erstweg" offers the street.

    A street name can itself contain numbers, so only a *trailing* one
    counts.
    """
    assert split_query(query) == expected


def test_typing_more_leaves_fewer_candidates(address_register):
    """The whole point of the feature, stated as a test."""
    few = suggest_addresses("Er", path=address_register)
    fewer = suggest_addresses("Erstw", path=address_register)

    assert len(few) >= len(fewer) >= 1


def test_whole_streets_are_offered_until_a_number_is_typed(address_register):
    """A street with sixty houses would bury the list before the number."""
    without = suggest_addresses("Erstweg", path=address_register)
    with_number = suggest_addresses("Erstweg 4", path=address_register)

    assert all(s.house_number == "" for s in without)
    assert "Erstweg 4, 3048 Musterdorf" in _labels(with_number)


def test_a_suggestion_carries_the_postal_locality(address_register):
    """Never the municipality: this is the text that reaches the invoice."""
    suggestion = suggest_addresses("Erstweg", path=address_register)[0]

    assert suggestion.locality == "Musterdorf"
    assert "Grossgemeinde" not in suggestion.label


def test_umlauts_narrow_the_same_way_they_sort(address_register):
    """Typing "arni" has to find "Ärniweg" -- same folding as the lists."""
    assert any("Ärniweg" in label for label in _labels(suggest_addresses("arni", path=address_register)))


def test_one_character_offers_nothing(address_register):
    """Every street in the country is not a shortlist."""
    assert suggest_addresses("E", path=address_register) == []


def test_a_non_official_address_is_still_offered(address_register):
    """It exists, so hiding it would send the administrator looking."""
    assert "Erstweg 31.1, 3048 Musterdorf" in _labels(suggest_addresses("Erstweg 31", path=address_register))


def test_postal_codes_and_localities_narrow_from_either_side(address_register):
    """The PLZ and the Ort field both help, so both are wired."""
    by_code = suggest_localities("304", path=address_register)
    by_name = suggest_localities("Musterd", path=address_register)

    assert ("3048", "Musterdorf") in [(s.postal_code, s.locality) for s in by_code]
    assert ("3048", "Musterdorf") in [(s.postal_code, s.locality) for s in by_name]


def test_a_multi_word_locality_survives_into_the_suggestions(address_register):
    """ "Beispiel Dorf" is one locality, not a locality and a stray word."""
    assert ("3065", "Beispiel Dorf") in [
        (s.postal_code, s.locality) for s in suggest_localities("3065", path=address_register)
    ]


# --- Checking a finished address ------------------------------------------


def test_a_correct_address_produces_nothing(address_register):
    """Silence is the normal case; 86 of 92 real sites are already right."""
    assert verify("Erstweg", "4", "3048", "Musterdorf", path=address_register) == []


def test_a_misspelled_locality_is_offered_the_right_one(address_register):
    """The real finding this started from: a typo nobody spots by eye."""
    findings = verify("Erstweg", "4", "3048", "Musterdrof", path=address_register)

    assert [(f.field, f.suggestion) for f in findings] == [(FIELD_LOCALITY, "Musterdorf")]


def test_the_political_municipality_is_offered_the_postal_locality(address_register):
    """The administrator's own rule, and the reason it exists.

    "Grossgemeinde" is a perfectly correct municipality for this postal
    code. It is still not what belongs on the letter, because the resident
    lives in Musterdorf and reads the envelope.
    """
    findings = verify("Erstweg", "4", "3048", "Grossgemeinde", path=address_register)

    assert [(f.field, f.suggestion) for f in findings] == [(FIELD_LOCALITY, "Musterdorf")]


def test_the_postal_code_typed_into_the_locality_field_is_caught(address_register):
    """Found in the live data, and not a typo but a slipped field."""
    findings = verify("Erstweg", "4", "3048", "3048 Musterdorf", path=address_register)

    assert [(f.field, f.suggestion) for f in findings] == [(FIELD_LOCALITY, "Musterdorf")]


def test_any_of_several_localities_for_one_postal_code_is_accepted(address_register):
    """3065 is both "Bolligen" and "Bolligen Dorf", and both are correct.

    Flagging one of them would report sound data, which is how a check
    earns itself the habit of being ignored.
    """
    assert verify("Drittweg", "1", "3065", "Beispiel Dorf", path=address_register) == []


def test_an_unknown_street_is_reported(address_register):
    """With no suggestion when nothing is close -- a new building exists."""
    findings = verify("Nirgendweg", "1", "3048", "Musterdorf", path=address_register)

    assert [f.field for f in findings] == [FIELD_STREET]


def test_an_unknown_house_number_is_reported_separately(address_register):
    """Independently of the street, so one does not mask the other."""
    findings = verify("Erstweg", "999", "3048", "Musterdorf", path=address_register)

    assert [f.field for f in findings] == [FIELD_HOUSE_NUMBER]


def test_a_dotted_house_number_validates(address_register):
    """It would not if the number were split into figure and letter."""
    assert verify("Erstweg", "31.1", "3048", "Musterdorf", path=address_register) == []


def test_a_letter_suffix_validates(address_register):
    """ "8a" is as ordinary as "8"."""
    assert verify("Erstweg", "8a", "3048", "Musterdorf", path=address_register) == []


def test_a_wrong_street_and_a_wrong_locality_are_both_reported(address_register):
    """Two independent findings, so dismissing one cannot hide the other."""
    findings = verify("Nirgendweg", "1", "3048", "Falschdorf", path=address_register)

    assert {f.field for f in findings} == {FIELD_STREET, FIELD_LOCALITY}


def test_a_po_box_is_reported_and_proposes_nothing(address_register):
    """Legitimate, and the register cannot know it.

    One finding with no suggestion, which the UI turns into a single "Nein"
    that retires it for good -- the trade the administrator chose over
    leaving billing addresses unchecked.
    """
    findings = verify("Postfach", "", "3048", "Musterdorf", path=address_register)

    assert [(f.field, f.suggestion) for f in findings] == [(FIELD_STREET, "")]


# --- Without a register ---------------------------------------------------


def test_everything_goes_quiet_without_a_register(tmp_path):
    """The app has to work before the first download, and after a failure.

    An absent register is not evidence against an address, so verification
    reports nothing rather than flagging everything.
    """
    absent = tmp_path / "gibt-es-nicht.sqlite3"

    assert register_available(absent) is False
    assert suggest_addresses("Erstweg", path=absent) == []
    assert suggest_localities("3048", path=absent) == []
    assert verify("Erstweg", "4", "3048", "Musterdorf", path=absent) == []


def test_a_register_that_exists_is_reported_as_available(address_register):
    """The counterpart, so the test above cannot pass by accident."""
    assert register_available(address_register) is True


def test_a_street_that_exists_elsewhere_blames_the_postal_code(address_register):
    """Found by running the real data, and it was offering a harmful fix.

    The street was spelled perfectly; it simply sat behind a different
    postal code. Offered the closest street *within* the typed code, the
    register proposed a different real street -- they share the "strasse"
    ending, which alone carries the similarity score -- and a click would
    have written it into the record.

    Raising the cutoff cannot separate these: that bad suggestion scored
    0.733 while a genuine postal-code-in-the-locality-field error scores
    0.737. The street existing elsewhere is the fact that distinguishes
    them.
    """
    findings = verify("Drittweg", "1", "3048", "Musterdorf", path=address_register)

    assert [(f.field, f.value, f.suggestion) for f in findings] == [(FIELD_POSTAL_CODE, "3048", "3065")]


def test_a_wrong_postal_code_does_not_also_report_the_locality(address_register):
    """One mistake, one finding.

    Checking the locality against the localities of a postal code that is
    itself wrong would report a second finding for the same error.
    """
    findings = verify("Drittweg", "1", "3048", "Beispiel Dorf", path=address_register)

    assert [f.field for f in findings] == [FIELD_POSTAL_CODE]


def test_a_street_that_exists_nowhere_is_still_a_street_finding(address_register):
    """The genuine-typo path, which the postal-code check must not swallow."""
    findings = verify("Nirgendweg", "1", "3048", "Musterdorf", path=address_register)

    assert [f.field for f in findings] == [FIELD_STREET]


def test_a_misspelled_street_is_offered_the_right_spelling(address_register):
    """Above the stricter street cutoff, so a real typo still gets help."""
    findings = verify("Erstwg", "4", "3048", "Musterdorf", path=address_register)

    assert [(f.field, f.suggestion) for f in findings] == [(FIELD_STREET, "Erstweg")]


def test_an_unrelated_street_name_is_offered_nothing(address_register):
    """The stricter cutoff for streets, stated as a test.

    Every Swiss street ends in "strasse" or "weg", so the shared suffix
    alone pushes unrelated names over the default threshold.
    """
    findings = verify("Zwölfterweg", "1", "3048", "Musterdorf", path=address_register)

    assert [(f.field, f.suggestion) for f in findings] == [(FIELD_STREET, "")]
