"""Tests for app.logging_setup (rotating file logging, secret redaction)."""

import logging

from app.logging_setup import configure_logging


def _reset_root_logger():
    """Remove every handler so a previous test's setup can't bleed into the next.

    Returns:
        None.
    """
    root = logging.getLogger()
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)


def test_configure_logging_creates_the_log_directory_if_missing(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    assert not logs_dir.exists()
    monkeypatch.setattr("app.logging_setup.LOGS_DIR", logs_dir)

    configure_logging()
    _reset_root_logger()

    assert logs_dir.is_dir()
    assert (logs_dir / "app.log").exists()


def test_configure_logging_writes_to_the_log_file(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("app.logging_setup.LOGS_DIR", logs_dir)

    configure_logging()
    logging.getLogger("test.module").info("Testnachricht %s", 42)
    _reset_root_logger()

    content = (logs_dir / "app.log").read_text(encoding="utf-8")
    assert "Testnachricht 42" in content
    assert "INFO" in content
    assert "test.module" in content


def test_log_file_rotates_once_it_exceeds_one_megabyte(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("app.logging_setup.LOGS_DIR", logs_dir)

    configure_logging()
    logger = logging.getLogger("test.rotation")
    # Each line is long enough that a modest number of iterations comfortably
    # crosses the 1 MB threshold and forces at least one rotation.
    line = "x" * 1000
    for _ in range(1200):
        logger.info(line)
    _reset_root_logger()

    files = sorted(p.name for p in logs_dir.iterdir())
    assert "app.log" in files
    assert "app.log.1" in files
    # Never more than the active file plus two rotated backups.
    assert "app.log.3" not in files
    assert len(files) <= 3
    for path in logs_dir.iterdir():
        assert path.stat().st_size <= 1_000_000 + 2000  # small slack for the final write


def test_redacting_filter_strips_bearer_tokens_and_secrets(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("app.logging_setup.LOGS_DIR", logs_dir)

    configure_logging()
    logger = logging.getLogger("test.secrets")
    logger.info("Authorization header: Bearer abc123.def456-ghi_789")
    logger.info('Graph config: {"client_secret": "S3cr3tValue!"}')
    logger.info('Web registration config: {"leg_api_token": "tok_live_abcdef"}')
    _reset_root_logger()

    content = (logs_dir / "app.log").read_text(encoding="utf-8")
    assert "abc123.def456-ghi_789" not in content
    assert "S3cr3tValue!" not in content
    assert "tok_live_abcdef" not in content
    assert "***REDACTED***" in content


def test_httpx_and_httpcore_loggers_are_kept_below_debug(tmp_path, monkeypatch):
    """These libraries log full request/response detail (including the
    Authorization header used by app.emailing.graph_client) at DEBUG --
    the actual safeguard against ever writing a secret to the log is
    keeping them at WARNING, not just the regex redaction filter."""
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("app.logging_setup.LOGS_DIR", logs_dir)

    configure_logging()
    _reset_root_logger()

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
