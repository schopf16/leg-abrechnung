"""Generates the QRR payment reference number printed on QR-invoices.

A QRR reference is up to 26 digits plus one Modulo-10-recursive check digit
(computed here via `stdnum.ch.esr`, the same algorithm library `qrbill`
itself uses to validate references; both left-pad the payload with zeros
to the full 26 digits when formatting, so a shorter payload is not a
problem). It encodes the person's Kundennummer (rather than the internal
database person id) as its first digits -- deliberately, so that reading
the reference straight off a bank statement already tells you which
customer it belongs to, without a lookup -- plus the billing run and line
item ids, so a bank statement can be matched back to the exact invoice
without manual lookup.
"""

from stdnum.ch import esr

#: Width, in digits, of each encoded component before the check digit.
#: Kundennummer is itself always a 6-digit number (see
#: `app.models.person.generate_kundennummer`), so it fits this width
#: exactly; the three widths need not sum to any particular total since
#: `esr`/`qrbill` zero-pad the payload up to 26 digits automatically.
_KUNDENNUMMER_DIGITS = 6
_BILLING_RUN_DIGITS = 6
_ITEM_DIGITS = 12


def generate_qrr_reference(kundennummer: int, billing_run_id: int, item_id: int) -> str:
    """Build a unique, valid QRR reference for one invoice.

    Args:
        kundennummer: The billed person's 6-digit Kundennummer (see
            `app.models.person.Person.kundennummer`) -- embedded first so
            the customer is identifiable directly from the reference.
        billing_run_id: Database id of the billing run.
        item_id: Database id of the billing run line item (the invoice).

    Returns:
        A numeric string (payload digits + 1 check digit, zero-padded to
        27 digits total once passed through `qrbill`) suitable for
        `qrbill.QRBill(reference_number=...)`.

    Raises:
        ValueError: If any id is too large to fit its allotted digit width.
    """
    payload = (
        f"{kundennummer:0{_KUNDENNUMMER_DIGITS}d}"
        f"{billing_run_id:0{_BILLING_RUN_DIGITS}d}"
        f"{item_id:0{_ITEM_DIGITS}d}"
    )
    if len(payload) != _KUNDENNUMMER_DIGITS + _BILLING_RUN_DIGITS + _ITEM_DIGITS:
        raise ValueError(
            "One of kundennummer, billing_run_id or item_id is too large "
            "to encode in a QRR reference."
        )
    check_digit = esr.calc_check_digit(payload)
    return payload + check_digit
