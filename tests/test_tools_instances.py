import responses
from garm_mcp.garm_client import GarmClient
from garm_mcp.tools import list_instances_view, summarize_statuses, truncate_items

CLIENT = GarmClient(name="prod", base_url="https://g.example.com", token="t")


def test_summarize_counts_unknown_for_missing_status():
    data = [{"status": "running"}, {"status": "pending_create"}, {"name": "x"}]
    assert summarize_statuses(data) == {"running": 1, "pending_create": 1, "unknown": 1}


def test_truncate_items():
    items = [{"i": n} for n in range(251)]
    page, total, truncated = truncate_items(items)
    assert (len(page), total, truncated) == (250, 251, True)
    assert truncate_items(items[:10]) == (items[:10], 10, False)


@responses.activate
def test_status_filter_keeps_full_set_counts_and_redacts():
    data = [{"name": "a", "status": "running", "registration_token": "T"},
            {"name": "b", "status": "pending_create"},
            {"name": "c"}]
    responses.get("https://g.example.com/api/v1/instances", json=data)
    out = list_instances_view(CLIENT, status="running")
    assert [i["name"] for i in out["instances"]] == ["a"]
    assert out["instances"][0]["registration_token"] == "[REDACTED]"
    assert out["status_counts"] == {"running": 1, "pending_create": 1, "unknown": 1}
    assert out["total"] == 3 and out["truncated"] is False


@responses.activate
def test_truncation_counts_full_set():
    data = [{"name": f"n{n}", "status": "running"} for n in range(251)]
    responses.get("https://g.example.com/api/v1/instances", json=data)
    out = list_instances_view(CLIENT)
    assert out["total"] == 251 and out["truncated"] is True and len(out["instances"]) == 250
    assert out["status_counts"] == {"running": 251}


@responses.activate
def test_scaleset_filter_uses_scoped_endpoint():
    responses.get("https://g.example.com/api/v1/scalesets/42/instances", json=[{"status": "running"}])
    out = list_instances_view(CLIENT, scaleset_id=42)
    assert out["status_counts"] == {"running": 1}
