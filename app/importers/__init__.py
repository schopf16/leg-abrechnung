"""Meter-reading import: EBIX/SDAT-CH (primary) and CSV (fallback).

Every parser in this package turns a raw input file into a list of
:class:`app.importers.base.ParsedReading` -- the *only* interface the rest
of the application relies on. Nothing outside this package needs to know
anything about XML elements, OBIS codes or CSV column order.
"""
