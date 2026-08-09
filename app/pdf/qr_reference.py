"""Generates the QRR payment reference number printed on QR-invoices.

A QRR reference is 26 digits plus one Modulo-10-recursive check digit
(computed here via `stdnum.ch.esr`, the same algorithm library `qrbill`
itself uses to validate references). It encodes the person's Kundennummer
(rather than the internal database person id) as its first 8 digits --
deliberately, so that reading the reference straight off a bank statement
already tells you which customer it belongs to, without a lookup -- plus
the billing run and line item ids, so a bank statement can be matched back
to the exact invoice without manual lookup.
"""

from stdnum.ch import esr

#: Width, in digits, of each encoded component before the check digit.
#: The three widths sum to 26, the QRR payload length (27 digits total
#: once the check digit computed below is appended). Kundennummer is
#: itself always an 8-digit number (see `app.models.person.
#: generate_kundennummer`), so it fits this width exactly.
_KUNDENNUMMER_DIGITS = 8
_BILLING_RUN_DIGITS = 6
_ITEM_DIGITS = 12


def generate_qrr_reference(kundennummer: int, billing_run_id: int, item_id: int) -> str:
    """Build a unique, valid 27-digit QRR reference for one invoice.

    Args:
        kundennummer: The billed person's 8-digit Kundennummer (see
            `app.models.person.Person.kundennummer`) -- embedded first so
            the customer is identifiable directly from the reference.
        billing_run_id: Database id of the billing run.
        item_id: Database id of the billing run line item (the invoice).

    Returns:
        A 27-digit numeric string (26 payload digits + 1 check digit)
        suitable for `qrbill.QRBill(reference_number=...)`.

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
