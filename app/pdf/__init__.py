"""PDF generation (QR-invoices) and CSV reconciliation lists for billing runs.

This package knows about `app.models` dataclasses and produces files under
`output/`; it has no knowledge of the GUI and does not read from the
database itself (callers pass in already-loaded records).
"""
