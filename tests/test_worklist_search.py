"""The search box on the Aufnahmen and Austritte worklists.

Both lists had sorting and filters but no search, so finding one person among
ninety-one meant reading the list. The matcher is word-wise, which is the
whole point: a full name is two words and no single field holds both.
"""

from datetime import date

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain.global_search import person_matches
from app.models import person as person_repo
from app.models import person_offboarding as offboarding_repo
from app.models import person_onboarding as onboarding_repo
from app.models.person import Person


def _person(
    first_name: str = "Michael",
    last_name: str = "Test",
    company: str = "",
) -> Person:
    """Build an unpersisted person with only the name fields that matter."""
    return Person(
        id=1,
        salutation="",
        company=company,
        first_name=first_name,
        last_name=last_name,
        contact_email="",
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
    )


# --- The matcher -----------------------------------------------------------


def test_a_full_name_is_found_although_no_field_holds_it():
    """The reason this is word-wise: "Michael Test" spans two columns."""
    assert person_matches(_person(), "Michael Test")


def test_the_order_of_the_words_does_not_matter():
    """Nobody should have to remember whether the list is surname-first."""
    assert person_matches(_person(), "test michael")


def test_one_word_is_enough():
    """A partial name narrows the list, which is what a search is for."""
    assert person_matches(_person(), "micha")


def test_a_word_that_matches_nothing_rejects_the_person():
    """Every word has to land, or two names would match everybody."""
    assert not person_matches(_person(), "Michael Beispiel")


def test_the_search_folds_like_the_sorting_does():
    """ "Buhler" finds "Bühler", the way the lists already sort it."""
    assert person_matches(_person(last_name="Bühler"), "buhler")


def test_a_company_is_searched_too():
    """The hint says Firma, so Firma has to work."""
    assert person_matches(_person(first_name="", last_name="", company="Beispiel AG"), "beispiel")


def test_an_empty_query_matches_everybody():
    """So the caller needs no special case for the cleared box."""
    assert person_matches(_person(), "")
    assert person_matches(_person(), "   ")


# --- The box on the page ---------------------------------------------------


def _seed(first_name: str, last_name: str) -> int:
    """Persist one person with an onboarding and an offboarding."""
    with connection_scope() as connection:
        person_id = person_repo.create(connection, _person(first_name=first_name, last_name=last_name))
        onboarding_repo.start_for_person(connection, person_id, registered_at=date(2026, 1, 1))
        offboarding_repo.start_for_person(connection, person_id, reason="voluntary")
        return person_id


def _names_on_page(page, route: str, query: str) -> list[str]:
    """Render one worklist with a query typed in, and read the names off it."""
    client = Client(ui.page(route)(lambda: None), request=None)
    with client:
        page()
        search = next(
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Input" and element.label == "Suche"
        )
        # Assigning `value` is what typing does -- NiceGUI fires the change
        # handlers from the setter, which is the wiring under test.
        search.value = query
        return [
            element.text
            for element in client.elements.values()
            if element.__class__.__name__ == "Link" and element.text
        ]


def test_the_aufnahmen_list_can_be_searched_by_full_name():
    """The administrator's own case: finding their test user among the rest."""
    _seed("Michael", "Test")
    _seed("Anna", "Beispiel")

    from app.gui.pages.onboardings import onboardings_page

    names = _names_on_page(onboardings_page, "/probe-worklist-search-on", "Michael Test")

    assert any("Test" in name for name in names), names
    assert not any("Beispiel" in name for name in names), names


def test_the_austritte_list_searches_the_same_way():
    """One mechanism: a list that searches differently is one more to learn."""
    _seed("Michael", "Test")
    _seed("Anna", "Beispiel")

    from app.gui.pages.offboardings import offboardings_page

    names = _names_on_page(offboardings_page, "/probe-worklist-search-off", "Michael Test")

    assert any("Test" in name for name in names), names
    assert not any("Beispiel" in name for name in names), names
