"""Persistence layer: typed records and CRUD repositories over SQLite.

Each module owns one table and exposes plain dataclasses plus small
repository functions (``list_all``, ``get``, ``create``, ``update``,
``delete``). No business logic lives here -- see :mod:`app.domain` for the
distribution and billing engine.
"""
