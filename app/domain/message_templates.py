"""Reading a Textbaustein's text, for the places that send a document."""

import sqlite3

from app.models import message_template as template_repo


def text_for(connection: sqlite3.Connection, occasion: str) -> tuple[str, str]:
    """The subject and body stored for one occasion."""
    templates = template_repo.list_for_occasion(connection, occasion)
    if not templates:
        return "", ""
    return templates[0].subject, templates[0].body
