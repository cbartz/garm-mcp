import base64
import json
import time

import pytest

from garm_mcp.config import Config, DeploymentConfig


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_jwt(payload: dict) -> str:
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(payload).encode())
    return f"{header}.{body}."


def _config(prod_exp_delta: int):
    now = int(time.time())
    return Config(
        bind_address="127.0.0.1:8080",
        endpoint_token="endpoint-secret-0123456789abcdef012345",
        deployments={
            "prod": DeploymentConfig(
                name="prod",
                base_url="https://prod.example.com",
                token=make_jwt({"exp": now + prod_exp_delta}),
            ),
            "staging": DeploymentConfig(
                name="staging",
                base_url="https://staging.example.com",
                token=make_jwt({"exp": now + 3600}),
            ),
        },
        default_deployment="prod",
    )


@pytest.fixture
def CONFIG():
    return _config(3600)


@pytest.fixture
def EXPIRED_CONFIG():
    return _config(-10)
