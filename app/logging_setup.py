"""Configures persistent, size-rotated file logging for the whole app."""

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
    """Scrubs known secret patterns out of a record's already-formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact known secret patterns in place, then let the record through."""
        message = record.getMessage()
        for pattern, replacement in _REDACTION_PATTERNS:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


def configure_logging() -> None:
    """Set up root logging: console + a rotating, secret-redacted file."""
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
