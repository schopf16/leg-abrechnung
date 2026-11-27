"""Transport-neutral email errors: callers catch these, whichever way the mail goes out.

A `MailAuthError` means no further message can succeed (wrong credentials, sender refused), so a
batch send aborts; a `MailError` concerns one message only, so a batch skips that recipient.
"""


class MailError(Exception):
    """One message could not be sent; the next one may still work."""


class MailAuthError(MailError):
    """The transport refused us as a whole -- credentials, permission or sender address."""
