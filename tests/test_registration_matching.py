"""Tests for `app.domain.registration_matching`.

Covers what the Web-Registrierungen inbox actually decides: which
existing record a registration matches, and which actions that leaves the
administrator. Both are pure functions over loaded rows, so none of this
needs a rendered page.
"""

import pytest

from app.domain.registration_matching import (
    decide_take_over,
    designation_key,
    email_key,
    load_matches,
    site_key,
)
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models.metering_point import DIRECTION_CONSUMPTION, MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.web_registration import WebRegistration, WebRegistrationMeter


# -- normalisation -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("anna@example.ch", "anna@example.ch"),
        ("Anna@Example.CH", "anna@example.ch"),
        ("  a@b.ch  ", "a@b.ch"),
        ("", ""),
        (None, ""),
    ],
)
def test_email_key_folds_case_and_padding(raw, expected):
    """A capitalised spelling must not create a second Person -- and it
    would, since `app.gui.person_form` has no duplicate-email check."""
    assert email_key(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("ch1001", "CH1001"), (" ch1001 ", "CH1001"), ("CH1001", "CH1001"), (None, "")],
)
def test_designation_key_matches_how_the_app_stores_it(raw, expected):
    """`assemble_metering_point_designation` stores designations
    uppercased, and `designation` is UNIQUE -- so a lowercase submission
    of an existing Messpunkt has to find it rather than run into the
    constraint in the create form."""
    assert designation_key(raw) == expected


def test_site_key_folds_case_and_padding():
    assert site_key(" Fischrain ", "68", "3063") == site_key("fischrain", " 68 ", " 3063")


# -- decide_take_over: the three states --------------------------------------


def test_done_offers_nothing_but_the_badge():
    choice = decide_take_over(done=True, existing="Fischrain 68", match_is_identity=True)

    assert choice.done
    assert not choice.offer_create
    assert not choice.offer_link
    assert not choice.offer_hand_mark


def test_an_identifying_match_offers_linking_and_suppresses_creating():
    """The reported bug: the second member of an apartment block was
    offered a create dialog whose only honest outcome was a duplicate
    site at the same address."""
    choice = decide_take_over(done=False, existing="Fischrain 68", match_is_identity=True)

    assert choice.offer_link
    assert not choice.offer_create


def test_a_merely_suggested_match_keeps_creating_available():
    """An email is not an identity (see `person_repo.get_by_email`): a
    household sharing one address resolves to whoever registered first,
    so the administrator must still be able to create the other person."""
    choice = decide_take_over(done=False, existing="Hans Muster", match_is_identity=False)

    assert choice.offer_link
    assert choice.offer_create


def test_no_match_offers_creating_and_the_hand_marking_escape_hatch():
    """A typo in a submitted address must not leave the entry stuck."""
    choice = decide_take_over(done=False, existing=None, match_is_identity=True)

    assert choice.offer_create
    assert choice.offer_hand_mark
    assert not choice.offer_link


def test_no_state_ever_leaves_an_item_without_a_way_to_close_it():
    """The dead end this whole change exists to remove: every state must
    offer at least one action that ends with the item closed."""
    for done in (True, False):
        for existing in (None, "Irgendetwas"):
            for identity in (True, False):
                choice = decide_take_over(done=done, existing=existing, match_is_identity=identity)
                closable = choice.done or choice.offer_link or choice.offer_create or choice.offer_hand_mark
                assert closable, (done, existing, identity)


# -- load_matches against a real database ------------------------------------


def _person(db, email, first_name="Hans", last_name="Muster"):
    person_repo.create(
        db,
        Person(
            id=None,
            salutation="",
            company="",
            first_name=first_name,
            last_name=last_name,
            contact_email=email,
            contact_phone="",
            billing_street="",
            billing_house_number="",
            billing_postal_code="",
            billing_city="",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
    )


def _site(db, street="Fischrain", house_number="68", postal_code="3063"):
    return site_repo.create(
        db,
        Site(
            id=None,
            street=street,
            house_number=house_number,
            postal_code=postal_code,
            municipality="Ittigen",
            address_detail="",
            substation_area_id=None,
            created_at="",
        ),
    )


def _registration(
    reg_id=1, email="anna@example.ch", street="Fischrain", house_number="68", postal_code="3063", meters=()
):
    return WebRegistration(
        id=reg_id,
        cloudflare_id=reg_id,
        company="",
        salutation="",
        first_name="Anna",
        last_name="Muster",
        street=street,
        house_number=house_number,
        postal_code=postal_code,
        city="Ittigen",
        email=email,
        phone="",
        bkw_customer_number="",
        iban="",
        message="",
        submitted_at="2026-01-01T10:00:00",
        imported_at="",
        meters=[
            WebRegistrationMeter(id=i + 1, web_registration_id=reg_id, meter_number=m, note="")
            for i, m in enumerate(meters)
        ],
    )


def test_the_second_registration_in_one_block_finds_the_shared_site(db):
    """The exact case reported from real use."""
    _site(db)

    match = load_matches(db, [_registration()])[1]

    assert match.site is not None
    assert match.site.full_address == "Fischrain 68, 3063 Ittigen"


def test_a_differently_capitalised_email_still_finds_the_person(db):
    _person(db, "anna@example.ch")

    match = load_matches(db, [_registration(email="Anna@Example.CH")])[1]

    assert match.person is not None


def test_a_lowercase_meter_number_still_finds_the_metering_point(db):
    site_id = _site(db)
    metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            site_id=site_id,
            designation="CH1001",
            direction=DIRECTION_CONSUMPTION,
            leg_id=None,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )

    match = load_matches(db, [_registration(meters=("ch1001",))])[1]

    assert match.metering_points[1] is not None


def test_a_typo_in_the_street_deliberately_finds_nothing(db):
    """No fuzzy matching: linking to the wrong address is worse than not
    finding it. Hand-marking covers this instead."""
    _site(db, street="Fischrain")

    match = load_matches(db, [_registration(street="Fishrain")])[1]

    assert match.site is None


def test_an_empty_email_matches_no_person(db):
    """An empty address is not an identity -- a Person without an email
    must not match every registration that lacks one."""
    _person(db, "")

    match = load_matches(db, [_registration(email="")])[1]

    assert match.person is None


def test_persons_sharing_one_email_resolve_deterministically(db):
    """`contact_email` is not unique. Which of them a dialog names must
    not depend on `list_all`'s ordering changing under us."""
    _person(db, "haushalt@example.ch", first_name="Hans", last_name="Muster")
    _person(db, "haushalt@example.ch", first_name="Anna", last_name="Zwahlen")

    first = load_matches(db, [_registration(email="haushalt@example.ch")])[1].person
    second = load_matches(db, [_registration(email="haushalt@example.ch")])[1].person

    assert first.id == second.id
