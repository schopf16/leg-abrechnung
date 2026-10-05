"""Reading a Textbaustein's text, for the places that send a document.

The invoice and the two dunning notices used to carry their text as column
pairs on `leg_settings`, edited on the Einstellungen page. They are
`message_template` rows now (migration 52 moved them, `app.db.schema.
_seed_message_templates` carried the wording over), so the Einstellungen
page no longer holds them -- and this is the one place that looks them up,
rather than each sender reaching into the table itself.

**A missing template is not an error here.** A text can be deleted on the
Textbausteine page, and the send paths each have their own moment to say so:
the invoice dialog shows the subject and body before anything leaves, and
the dunning preview does the same. Returning empty text lets that moment
happen instead of raising somewhere the administrator cannot see.
"""

import sqlite3

from app.models import message_template as template_repo


def text_for(connection: sqlite3.Connection, occasion: str) -> tuple[str, str]:
    """The subject and body stored for one occasion.

    Takes the first template of that occasion. Several are allowed per
    occasion -- that is what makes a reminder possible for a process step --
    but the invoice and the dunning notices have exactly one each, and the
    send paths there do not ask which.

    Args:
        connection: Open SQLite connection.
        occasion: One of `app.models.message_template`'s `OCCASION_*`.

    Returns:
        `(subject, body)`, still carrying their `{placeholder}`s, or
        `("", "")` when no template exists for that occasion.
    """
    templates = template_repo.list_for_occasion(connection, occasion)
    if not templates:
        return "", ""
    return templates[0].subject, templates[0].body
