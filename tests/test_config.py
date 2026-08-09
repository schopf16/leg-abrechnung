"""Tests for app.config.get_leg_api_token (local, gitignored secrets file)."""

import json

import pytest

from app import config as config_module
from app.config import ConfigError, get_leg_api_token


def test_get_leg_api_token_raises_if_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", tmp_path / "config.local.json")
    with pytest.raises(ConfigError, match="config.local.json"):
        get_leg_api_token()


def test_get_leg_api_token_raises_on_invalid_json(monkeypatch, tmp_path):
    path = tmp_path / "config.local.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    with pytest.raises(ConfigError):
        get_leg_api_token()


def test_get_leg_api_token_raises_if_key_missing(monkeypatch, tmp_path):
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    with pytest.raises(ConfigError):
        get_leg_api_token()


def test_get_leg_api_token_raises_if_key_empty(monkeypatch, tmp_path):
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps({"leg_api_token": "   "}), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    with pytest.raises(ConfigError):
        get_leg_api_token()


def test_get_leg_api_token_returns_configured_value(monkeypatch, tmp_path):
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps({"leg_api_token": "secret-value"}), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    assert get_leg_api_token() == "secret-value"
