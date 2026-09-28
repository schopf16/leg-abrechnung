"""The letter salutation for a Person -- one function, used by both the PDF
and the email templates.

Lives here rather than in `app/pdf/` or `app/emailing/` because both need
it and neither may import the other. A document and the mail announcing it
must not greet the same person differently.

**Why "Guten Tag" and not "Sehr geehrte...".** German adjective inflection
has to agree with the person's gender, and every mechanism this app had for
that was wrong:

  - The billing PDF printed a fixed `"Sehr geehrte Kundin, sehr geehrter
    Kunde"` -- correct for nobody in particular, and it names no name at
    all.
  - The email templates let the administrator build the salutation from
    parts, so `"Sehr geehrte {anrede} {nachname}"` came out as `"Sehr
    geehrte Herr Muster"` for every man who ever received one.

With two named people (a couple, see `app.models.person.Person`) the
part-assembly approach cannot work at all, and neither can a single
inflected adjective. "Guten Tag" carries no adjective, so one rule covers
every constellation: one person or two, any combination of salutations, a
salutation nobody recorded, and someone who does not want to be sorted into
Herr or Frau. An empty salutation field is therefore a valid state here,
not a defect to be fixed before a letter can go out.

Swiss usage: "Herr", not the German dative "Herrn".
"""

from app.models.person import NamedPerson, Person

#: The greeting that opens the line.
_GREETING = "Guten Tag"

#: The same greeting for a second person mid-sentence. Written out rather
#: than lower-cased from `_GREETING`, because "Tag" is a noun and stays
#: capitalised -- `.lower()` produced "guten tag".
_GREETING_CONTINUED = "guten Tag"


def _addressee(person: NamedPerson) -> str:
    """How one named person is referred to in a salutation.

    Args:
        person: The named person to address.

    Returns:
        `"Frau Muster"` when a salutation is known, otherwise the full name
        (`"Anna Muster"`), or `""` if the person carries no name at all.
        The surname alone follows the salutation -- "Guten Tag Frau Anna
        Muster" is not how anyone is addressed -- but a person recorded
        with only a first name keeps it, because dropping it would leave
        the salutation standing on its own.
    """
    if person.salutation:
        return f"{person.salutation} {person.last_name or person.first_name}".strip()
    return person.full_name


def letter_salutation(person: Person) -> str:
    """The salutation line for a Person, without trailing punctuation.

    Args:
        person: The Person being written to. Both named people are greeted
            if the record holds two (see `Person.named_persons`).

    Returns:
        E.g. `"Guten Tag Frau Muster"`, `"Guten Tag Frau Muster, guten Tag
        Herr Beispiel"`, or the bare `"Guten Tag"` for a company with no
        named contact person -- never an empty string, because a letter
        needs a salutation even when the app knows no name.
    """
    addressees = [_addressee(named) for named in person.named_persons]
    addressees = [name for name in addressees if name]
    if not addressees:
        return _GREETING
    parts = [f"{_GREETING} {addressees[0]}"]
    parts.extend(f"{_GREETING_CONTINUED} {name}" for name in addressees[1:])
    return ", ".join(parts)
