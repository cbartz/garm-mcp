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


def _write(tmp_path, data):
    p = tmp_path / "c.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return str(p)


@pytest.mark.parametrize("value", ["", "   ", "CHANGE_ME", "change_me", " Change_Me "])
def test_placeholder_or_empty_endpoint_token_raises(tmp_path, value):
    path = _write(tmp_path, dict(VALID, endpoint_token=value))
    with pytest.raises(ConfigError, match="endpoint_token must be set to a strong random value"):
        load_config(path)


@pytest.mark.parametrize("auth, key", [
    ({"username": "", "password": "p"}, "username"),
    ({"username": "u", "password": ""}, "password"),
    ({"token": ""}, "token"),
])
def test_empty_credential_raises(tmp_path, auth, key):
    bad = dict(VALID, deployments={"prod": {"base_url": "https://p.example.com", **auth}})
    with pytest.raises(ConfigError, match=f"deployment 'prod' has empty '{key}'"):
        load_config(_write(tmp_path, bad))


def test_password_auth_loads(tmp_path):
    ok = dict(VALID, deployments={"prod": {"base_url": "https://p.example.com",
                                           "username": "admin", "password": "pw"}})
    cfg = load_config(_write(tmp_path, ok))
    dep = cfg.deployments["prod"]
    assert (dep.username, dep.password, dep.token) == ("admin", "pw", None)


def test_malformed_json_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="invalid config"):
        load_config(_write(tmp_path, "{not json"))


def test_non_object_top_level_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="invalid config"):
        load_config(_write(tmp_path, "[1, 2]"))


@pytest.mark.parametrize("deployments", [["prod"], {"prod": "oops"}])
def test_non_dict_deployments_raise_config_error(tmp_path, deployments):
    with pytest.raises(ConfigError, match="invalid config"):
        load_config(_write(tmp_path, dict(VALID, deployments=deployments)))


def test_allowed_hosts_defaults_empty_and_loads(tmp_path):
    assert load_config(_write(tmp_path, VALID)).allowed_hosts == []
    cfg = load_config(_write(tmp_path, dict(VALID, allowed_hosts=["vmhost.local:9000"])))
    assert cfg.allowed_hosts == ["vmhost.local:9000"]


@pytest.mark.parametrize("value", ["vmhost", [1], None])
def test_allowed_hosts_must_be_list_of_strings(tmp_path, value):
    with pytest.raises(ConfigError, match="allowed_hosts must be a list of strings"):
        load_config(_write(tmp_path, dict(VALID, allowed_hosts=value)))


def _cfg_with(**overrides):
    cfg = json.loads(json.dumps(VALID))
    cfg.update(overrides)
    return cfg


def test_non_string_base_url_raises(tmp_path):
    cfg = _cfg_with()
    cfg["deployments"]["prod"]["base_url"] = None
    p = tmp_path / "config.json"; p.write_text(json.dumps(cfg))
    with pytest.raises(ConfigError, match="deployment 'prod' has invalid 'base_url'"):
        load_config(str(p))


def test_blank_base_url_raises(tmp_path):
    cfg = _cfg_with()
    cfg["deployments"]["prod"]["base_url"] = "   "
    p = tmp_path / "config.json"; p.write_text(json.dumps(cfg))
    with pytest.raises(ConfigError, match="deployment 'prod' has invalid 'base_url'"):
        load_config(str(p))


def test_ca_bundle_non_string_raises(tmp_path):
    cfg = _cfg_with()
    cfg["deployments"]["prod"]["ca_bundle"] = 123
    p = tmp_path / "config.json"; p.write_text(json.dumps(cfg))
    with pytest.raises(ConfigError, match="deployment 'prod' has invalid 'ca_bundle'"):
        load_config(str(p))


def test_blank_ca_bundle_raises(tmp_path):
    cfg = _cfg_with()
    cfg["deployments"]["prod"]["ca_bundle"] = "  "
    p = tmp_path / "config.json"; p.write_text(json.dumps(cfg))
    with pytest.raises(ConfigError, match="deployment 'prod' has invalid 'ca_bundle'"):
        load_config(str(p))


def test_bind_address_without_port_raises(tmp_path):
    p = tmp_path / "config.json"; p.write_text(json.dumps(_cfg_with(bind_address="localhost")))
    with pytest.raises(ConfigError, match="bind_address 'localhost' is not 'host:port'"):
        load_config(str(p))


@pytest.mark.parametrize("value", ["h:0", "h:70000", "h:abc"])
def test_bind_address_bad_port_raises(tmp_path, value):
    p = tmp_path / "config.json"; p.write_text(json.dumps(_cfg_with(bind_address=value)))
    with pytest.raises(ConfigError, match="invalid port"):
        load_config(str(p))


def test_bind_address_ipv6_brackets_raises(tmp_path):
    p = tmp_path / "config.json"; p.write_text(json.dumps(_cfg_with(bind_address="[::1]:9000")))
    with pytest.raises(ConfigError, match="bracketed IPv6 hosts are not supported"):
        load_config(str(p))


def test_bind_address_wildcard_ok(tmp_path):
    p = tmp_path / "config.json"; p.write_text(json.dumps(_cfg_with(bind_address="0.0.0.0:9000")))
    assert load_config(str(p)).bind_address == "0.0.0.0:9000"


@pytest.mark.parametrize("exp", ["tomorrow", None, True])
def test_exp_non_numeric_raises(exp):
    with pytest.raises(ConfigError, match="cannot parse GARM token for deployment 'prod'"):
        check_token_expiry("prod", make_jwt({"exp": exp}))


def test_allowed_origins_key_loads(tmp_path):
    cfg = _cfg_with(allowed_origins=["https://garm.example.com"])
    p = tmp_path / "config.json"; p.write_text(json.dumps(cfg))
    assert load_config(str(p)).allowed_origins == ["https://garm.example.com"]


def test_allowed_origins_must_be_list_of_strings(tmp_path):
    p = tmp_path / "config.json"; p.write_text(json.dumps(_cfg_with(allowed_origins="http://x")))
    with pytest.raises(ConfigError, match="allowed_origins must be a list of strings"):
        load_config(str(p))
