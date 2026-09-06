"""Tests for app.config's local, gitignored secrets file (leg_api_token,
Microsoft Graph credentials)."""

import json

import pytest

from app import config as config_module
from app.config import ConfigError, GraphConfig, get_graph_config, get_leg_api_token

_VALID_GRAPH_DATA = {
    "graph_tenant_id": "tenant-123",
    "graph_client_id": "client-456",
    "graph_client_secret": "secret-789",
    "graph_sender_address": "leg@example.invalid",
    "graph_sender_name": "LEG Test",
}


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


def test_get_graph_config_raises_if_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", tmp_path / "config.local.json")
    with pytest.raises(ConfigError, match="config.local.json"):
        get_graph_config()


def test_get_graph_config_raises_on_invalid_json(monkeypatch, tmp_path):
    path = tmp_path / "config.local.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    with pytest.raises(ConfigError):
        get_graph_config()


@pytest.mark.parametrize(
    "missing_key",
    ["graph_tenant_id", "graph_client_id", "graph_client_secret", "graph_sender_address"],
)
def test_get_graph_config_raises_if_required_key_missing(monkeypatch, tmp_path, missing_key):
    data = dict(_VALID_GRAPH_DATA)
    del data[missing_key]
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    with pytest.raises(ConfigError, match=missing_key):
        get_graph_config()


def test_get_graph_config_raises_if_required_key_empty(monkeypatch, tmp_path):
    data = dict(_VALID_GRAPH_DATA)
    data["graph_client_secret"] = "   "
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    with pytest.raises(ConfigError):
        get_graph_config()


def test_get_graph_config_sender_name_is_optional(monkeypatch, tmp_path):
    data = dict(_VALID_GRAPH_DATA)
    del data["graph_sender_name"]
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    assert get_graph_config().sender_name == ""


def test_get_graph_config_returns_configured_values(monkeypatch, tmp_path):
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps(_VALID_GRAPH_DATA), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    assert get_graph_config() == GraphConfig(
        tenant_id="tenant-123",
        client_id="client-456",
        client_secret="secret-789",
        sender_address="leg@example.invalid",
        sender_name="LEG Test",
    )
