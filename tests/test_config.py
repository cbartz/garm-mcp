import json
import time

import pytest
from conftest import make_jwt

from garm_mcp.config import load_config, check_token_expiry
from garm_mcp.errors import ConfigError

VALID = {"bind_address": "127.0.0.1:9000", "endpoint_token": "ep-token",
         "default_deployment": "prod",
         "deployments": {"prod": {"base_url": "https://p.example.com", "token": "t1", "ca_bundle": None},
                         "staging": {"base_url": "https://s.example.com", "token": "t2", "ca_bundle": None}}}

def test_load_config_valid(tmp_path):
    p = tmp_path / "config.json"; p.write_text(json.dumps(VALID))
    cfg = load_config(str(p))
    assert cfg.bind_address == "127.0.0.1:9000" and cfg.endpoint_token == "ep-token"
    assert set(cfg.deployments) == {"prod", "staging"}
    assert cfg.deployments["prod"].base_url == "https://p.example.com"

def test_load_config_missing_file_raises():
    with pytest.raises(ConfigError, match="config file not found"):
        load_config("/nonexistent/config.json")

def test_load_config_missing_key_raises(tmp_path):
    p = tmp_path / "c.json"; p.write_text('{"bind_address": "h:1"}')
    with pytest.raises(ConfigError, match="missing required config key: endpoint_token"):
        load_config(str(p))

def test_load_config_bad_default_raises(tmp_path):
    bad = dict(VALID, default_deployment="nope")
    p = tmp_path / "c.json"; p.write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match="default_deployment 'nope' is not a configured deployment"):
        load_config(str(p))

def test_expired_token_raises():
    payload = {"exp": int(time.time()) - 10}
    with pytest.raises(ConfigError, match="expired at"):
        check_token_expiry("prod", make_jwt(payload))

def test_token_without_exp_raises():
    with pytest.raises(ConfigError, match="no exp claim"):
        check_token_expiry("prod", make_jwt({"sub": "x"}))

def test_unparseable_token_raises():
    with pytest.raises(ConfigError, match="cannot parse GARM token for deployment 'prod'"):
        check_token_expiry("prod", "not-a-jwt")

def test_deployment_without_auth_raises(tmp_path):
    bad = dict(VALID)
    bad["deployments"] = {"prod": {"base_url": "https://p.example.com", "ca_bundle": None}}
    p = tmp_path / "c.json"; p.write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match="needs either 'token' or 'username' and 'password'"):
        load_config(str(p))

def test_deployment_with_both_auth_modes_raises(tmp_path):
    bad = dict(VALID)
    bad["deployments"] = {"prod": {"base_url": "https://p.example.com", "token": "t",
                                   "username": "u", "password": "p", "ca_bundle": None}}
    p = tmp_path / "c.json"; p.write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match="must not set both"):
        load_config(str(p))
