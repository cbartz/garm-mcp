# tests/test_garm_client.py
import pytest
import requests, responses
from garm_mcp.garm_client import GarmClient
from garm_mcp.errors import GarmApiError

BASE = "https://garm.example.com"
CLIENT = dict(name="prod", base_url=BASE, token="jwt1")

@responses.activate
def test_list_instances_sends_bearer_and_parses():
    responses.get(f"{BASE}/api/v1/instances", json=[{"name": "i1"}, {"name": "i2"}])
    out = GarmClient(**CLIENT).list_instances()
    assert [i["name"] for i in out] == ["i1", "i2"]
    assert responses.calls[0].request.headers["Authorization"] == "Bearer jwt1"

@responses.activate
def test_get_instance_uses_encoded_name_path():
    responses.get(f"{BASE}/api/v1/instances/runner-abc", json={"name": "runner-abc"})
    assert GarmClient(**CLIENT).get_instance("runner-abc")["name"] == "runner-abc"

@responses.activate
def test_scaleset_instances_url():
    responses.get(f"{BASE}/api/v1/scalesets/42/instances", json=[])
    assert GarmClient(**CLIENT).list_scaleset_instances(42) == []

@responses.activate
def test_api_error_maps_message_and_status():
    responses.get(f"{BASE}/api/v1/jobs", json={"error": {"message": "pool not found", "type": 0}}, status=400)
    with pytest.raises(GarmApiError, match="pool not found") as ei:
        GarmClient(**CLIENT).list_jobs()
    assert ei.value.status_code == 400

@responses.activate
def test_transient_failure_retries_once():
    responses.get(f"{BASE}/api/v1/instances", body=requests.ConnectionError())
    responses.get(f"{BASE}/api/v1/instances", json=[])
    client = GarmClient(**CLIENT, retry_delay=0.01)
    assert client.list_instances() == []
    assert len(responses.calls) == 2

@responses.activate
def test_non_json_error_falls_back():
    responses.get(f"{BASE}/api/v1/instances", body="boom", status=502)
    responses.get(f"{BASE}/api/v1/instances", body="boom", status=502)
    with pytest.raises(GarmApiError, match="unknown error \\(HTTP 502\\)"):
        GarmClient(**CLIENT, retry_delay=0.01).list_instances()

def test_ca_bundle_sets_verify():
    c = GarmClient(**CLIENT, ca_bundle="/etc/ca.pem")
    assert c.session.verify == "/etc/ca.pem"

@responses.activate
def test_login_mints_and_caches_token():
    responses.post(f"{BASE}/api/v1/auth/login", json={"token": "fresh"})
    responses.get(f"{BASE}/api/v1/instances", json=[])
    client = GarmClient(name="prod", base_url=BASE, username="admin", password="pw")
    assert client.list_instances() == []
    assert responses.calls[1].request.headers["Authorization"] == "Bearer fresh"

@responses.activate
def test_401_triggers_relogin_once():
    responses.get(f"{BASE}/api/v1/instances", json={"error": {"message": "expired", "type": 0}}, status=401)
    responses.post(f"{BASE}/api/v1/auth/login", json={"token": "fresh2"})
    responses.get(f"{BASE}/api/v1/instances", json=[{"name": "i1"}])
    client = GarmClient(name="prod", base_url=BASE, token="stale", username="admin", password="pw")
    assert [i["name"] for i in client.list_instances()] == ["i1"]
    assert responses.calls[2].request.headers["Authorization"] == "Bearer fresh2"

@responses.activate
def test_401_without_credentials_raises():
    responses.get(f"{BASE}/api/v1/instances", json={"error": {"message": "unauthorized", "type": 0}}, status=401)
    with pytest.raises(GarmApiError, match="unauthorized"):
        GarmClient(**CLIENT).list_instances()


@responses.activate
def test_list_jobs_unwraps_paginated_response():
    responses.get(f"{BASE}/api/v1/jobs",
                  json={"total_count": 300, "pages": 2, "current_page": 1, "results": [{"id": 1}]})
    jobs, total = GarmClient(**CLIENT).list_jobs_page()
    assert [j["id"] for j in jobs] == [1]
    assert total == 300
    assert "pageSize=250" in responses.calls[0].request.url
    assert "all=true" in responses.calls[0].request.url


@responses.activate
def test_list_jobs_handles_legacy_list_response():
    responses.get(f"{BASE}/api/v1/jobs", json=[{"id": 1}, {"id": 2}])
    jobs, total = GarmClient(**CLIENT).list_jobs_page()
    assert len(jobs) == 2 and total is None


@responses.activate
def test_list_jobs_keeps_list_contract():
    responses.get(f"{BASE}/api/v1/jobs", json=[])
    assert GarmClient(**CLIENT).list_jobs() == []


@responses.activate
def test_flat_error_response_parses():
    responses.get(f"{BASE}/api/v1/instances",
                  json={"error": "Bad Request", "details": "bad pool"}, status=400)
    with pytest.raises(GarmApiError, match="Bad Request: bad pool") as ei:
        GarmClient(**CLIENT).list_instances()
    assert ei.value.status_code == 400
