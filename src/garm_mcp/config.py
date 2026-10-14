import base64
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from garm_mcp.errors import ConfigError

CONFIG_ENV = "GARM_MCP_CONFIG"
REQUIRED_KEYS = ("bind_address", "endpoint_token", "default_deployment", "deployments")


@dataclass
class DeploymentConfig:
    name: str
    base_url: str
    token: str | None = None
    username: str | None = None
    password: str | None = None
    ca_bundle: str | None = None


@dataclass
class Config:
    bind_address: str
    endpoint_token: str
    deployments: dict[str, DeploymentConfig] = field(default_factory=dict)
    default_deployment: str = ""
    allowed_hosts: list[str] = field(default_factory=list)


def _parse_deployment(name: str, raw: dict) -> DeploymentConfig:
    if "base_url" not in raw:
        raise ConfigError(f"deployment '{name}' missing required key: base_url")
    for key in ("token", "username", "password"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ConfigError(f"deployment '{name}' has non-string '{key}'")
        if not value:
            raise ConfigError(f"deployment '{name}' has empty '{key}'")
    token = raw.get("token")
    username = raw.get("username")
    password = raw.get("password")
    has_password_auth = username is not None and password is not None
    if token is not None and (username is not None or password is not None):
        raise ConfigError(f"deployment '{name}' must not set both 'token' and username/password")
    if token is None and not has_password_auth:
        raise ConfigError(f"deployment '{name}' needs either 'token' or 'username' and 'password'")
    return DeploymentConfig(
        name=name,
        base_url=raw["base_url"],
        token=token,
        username=username,
        password=password,
        ca_bundle=raw.get("ca_bundle"),
    )


def _validate_endpoint_token(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.strip().upper() == "CHANGE_ME"
    ):
        raise ConfigError(
            "endpoint_token must be set to a strong random value (see README host setup)"
        )
    return value


def _parse_config(raw: object) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("invalid config: top level must be a JSON object")
    for key in REQUIRED_KEYS:
        if key not in raw:
            raise ConfigError(f"missing required config key: {key}")
    endpoint_token = _validate_endpoint_token(raw["endpoint_token"])
    allowed_hosts = raw.get("allowed_hosts", [])
    if not isinstance(allowed_hosts, list) or not all(isinstance(h, str) for h in allowed_hosts):
        raise ConfigError("allowed_hosts must be a list of strings")
    if not isinstance(raw["deployments"], dict):
        raise ConfigError("invalid config: 'deployments' must be an object")
    deployments = {}
    for name, spec in raw["deployments"].items():
        if not isinstance(spec, dict):
            raise ConfigError(f"invalid config: deployment '{name}' must be an object")
        deployments[name] = _parse_deployment(name, spec)
    if raw["default_deployment"] not in deployments:
        raise ConfigError(
            f"default_deployment '{raw['default_deployment']}' is not a configured deployment"
        )
    return Config(
        bind_address=raw["bind_address"],
        endpoint_token=endpoint_token,
        deployments=deployments,
        default_deployment=raw["default_deployment"],
        allowed_hosts=allowed_hosts,
    )


def load_config(path: str | None = None) -> Config:
    path = path or os.environ.get(CONFIG_ENV)
    if not path:
        raise ConfigError(f"no config path given; pass one or set {CONFIG_ENV}")
    if not os.path.isfile(path):
        raise ConfigError(f"config file not found: {path}")
    try:
        with open(path) as fh:
            raw = json.load(fh)
        return _parse_config(raw)
    except ConfigError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise ConfigError(f"invalid config: {exc}") from exc


def check_token_expiry(name: str, token: str, now: float | None = None) -> None:
    try:
        segment = token.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment))
    except (IndexError, ValueError):
        raise ConfigError(f"cannot parse GARM token for deployment '{name}'") from None
    if "exp" not in claims:
        raise ConfigError(f"GARM token for deployment '{name}' has no exp claim; refusing token")
    exp = claims["exp"]
    if exp <= (now if now is not None else time.time()):
        expired_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
        raise ConfigError(
            f"GARM token for deployment '{name}' expired at {expired_at}; "
            "mint a longer-lived JWT (see README)"
        )
