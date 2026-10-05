"""The letter salutation for a Person -- one function, used by both the PDF and the email templates."""

from app.models.person import NamedPerson, Person

#: The greeting that opens the line.
_GREETING = "Guten Tag"

#: The same greeting for a second person mid-sentence. Written out rather
#: than lower-cased from `_GREETING`, because "Tag" is a noun and stays
#: capitalised -- `.lower()` produced "guten tag".
_GREETING_CONTINUED = "guten Tag"


def _addressee(person: NamedPerson) -> str:
    """How one named person is referred to in a salutation."""
    if person.salutation:
        return f"{person.salutation} {person.last_name or person.first_name}".strip()
    return person.full_name


def letter_salutation(person: Person) -> str:
    """The salutation line for a Person, without trailing punctuation."""
    addressees = [_addressee(named) for named in person.named_persons]
    addressees = [name for name in addressees if name]
    if not addressees:
        return _GREETING
    parts = [f"{_GREETING} {addressees[0]}"]
    parts.extend(f"{_GREETING_CONTINUED} {name}" for name in addressees[1:])
    return ", ".join(parts)
