import pytest
import responses

from garm_mcp.garm_client import GarmClient
from garm_mcp.tools import (
    get_controller_info_view,
    get_instance_view,
    list_jobs_view,
    list_view,
    resolve_deployment,
)

CLIENT = GarmClient(name="prod", base_url="https://g.example.com", token="t")


@responses.activate
def test_list_view_shape_and_redaction():
    responses.get("https://g.example.com/api/v1/scalesets",
                  json=[{"id": 1, "github_token": "T"}])
    out = list_view(CLIENT, "list_scalesets")
    assert out["total"] == 1 and out["truncated"] is False
    assert out["items"][0]["github_token"] == "[REDACTED]"


@responses.activate
def test_get_instance_view_redacts():
    responses.get("https://g.example.com/api/v1/instances/r1",
                  json={"name": "r1", "registration_token": "T"})
    assert get_instance_view(CLIENT, "r1")["registration_token"] == "[REDACTED]"


@responses.activate
def test_get_controller_info_view_redacts():
    responses.get("https://g.example.com/api/v1/controller-info",
                  json={"controller_id": "c1", "jwt_secret": "S"})
    out = get_controller_info_view(CLIENT)
    assert out["controller_id"] == "c1"
    assert "S" not in str(out)


def test_resolve_deployment_default_and_unknown(CONFIG):
    assert resolve_deployment(CONFIG, None).name == "prod"
    assert resolve_deployment(CONFIG, "staging").name == "staging"
    with pytest.raises(ValueError, match="unknown deployment 'nope'; configured:"):
        resolve_deployment(CONFIG, "nope")


@responses.activate
def test_jobs_view_paginated_shape():
    responses.get("https://g.example.com/api/v1/jobs",
                  json={"total_count": 300, "results": [{"status": "queued", "github_token": "T"}]})
    out = list_jobs_view(CLIENT)
    assert out["total"] == 300 and out["truncated"] is True and len(out["items"]) == 1
    assert out["items"][0]["github_token"] == "[REDACTED]"


@responses.activate
def test_jobs_view_legacy_shape_truncates():
    data = [{"name": f"j{n}"} for n in range(251)]
    responses.get("https://g.example.com/api/v1/jobs", json=data)
    out = list_jobs_view(CLIENT)
    assert out["total"] == 251 and out["truncated"] is True and len(out["items"]) == 250
