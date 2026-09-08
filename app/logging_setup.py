"""Configures persistent, size-rotated file logging for the whole app.

Michael cannot reliably copy an error out of the native window or the
console it runs behind, so every run instead writes to a small, rotating
log file he can just send along when something goes wrong -- see
`app.main.main` (calls `configure_logging()` first, before anything else)
and `app.gui.safe_notify` (used by the NiceGUI exception hook this module
wires up, once registered, so a previously silent callback failure now
also lands here).

`RotatingFileHandler` opens in append mode -- restarting the app (e.g. to
make a transient error go away) never erases what an earlier run already
wrote, only a rotation once a file passes `_MAX_BYTES` does. `app.main.
main` brackets every run with a `"="*80` + "START"/"ENDE" pair (PID,
Python/OS version included) specifically so a restart is still visible in
the log even once the on-screen symptom is gone -- a START line with no
matching "ENDE (regulär beendet)" before the next START means that run
crashed rather than exited cleanly.

Deliberately excludes anything from ``httpx``/``httpcore`` above
``WARNING`` -- their ``DEBUG`` level logs full request/response detail,
including the ``Authorization`` header used by `app.emailing.graph_client`.
That is the actual point secrets could leak from in this codebase; the
regex-based `_RedactingFilter` below is a second, defense-in-depth layer,
not the primary safeguard.
"""

import logging
import re
from logging.handlers import RotatingFileHandler

from app.paths import LOGS_DIR

#: 1 MB per file, two rotated backups kept (`app.log`, `app.log.1`,
#: `app.log.2`) -- three files, ~3 MB total, enough history for a support
#: request without growing unbounded on Michael's PC.
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 2

#: (pattern, replacement) pairs applied, in order, to every formatted log
#: line before it is written -- see the module docstring for why this is a
#: second layer on top of keeping httpx/httpcore below DEBUG.
_REDACTION_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer ***REDACTED***"),
    (re.compile(r'"?client_secret"?\s*[:=]\s*"?[^"&\s]+"?', re.IGNORECASE), "client_secret=***REDACTED***"),
    (re.compile(r'"?leg_api_token"?\s*[:=]\s*"?[^"&\s]+"?', re.IGNORECASE), "leg_api_token=***REDACTED***"),
]


class _RedactingFilter(logging.Filter):
    """Scrubs known secret patterns out of a record's already-formatted message.

    Applied as a `logging.Filter` (not a `Formatter`) so it can rewrite
    `record.msg`/`record.args` before any handler formats them -- both the
    file and console handler benefit, without duplicating the regex logic.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact known secret patterns in place, then let the record through.

        Args:
            record: The log record about to be emitted.

        Returns:
            Always `True` -- this filter only rewrites, never suppresses.
        """
        message = record.getMessage()
        for pattern, replacement in _REDACTION_PATTERNS:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


def configure_logging() -> None:
    """Set up root logging: console + a rotating, secret-redacted file.

    Idempotent-ish in practice (only ever called once, from `app.main.main`
    before anything else runs) but safe to call again -- `logging.
    basicConfig`-style duplicate handlers are avoided by always resetting
    the root logger's handlers first.

    Returns:
        None.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    redactor = _RedactingFilter()

    file_handler = RotatingFileHandler(
        LOGS_DIR / "app.log", maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redactor)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # These libraries only log request/response detail (headers included)
    # at DEBUG -- keeping them at WARNING is the actual safeguard against
    # ever writing a bearer token or client secret to the log, see the
    # module docstring.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
