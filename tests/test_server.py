import json

import pytest
import responses

from garm_mcp.errors import ConfigError
from garm_mcp.server import build_server

TOOL_NAMES = sorted([
    "garm_list_instances", "garm_get_instance", "garm_list_scalesets",
    "garm_list_jobs", "garm_list_repositories", "garm_list_organizations",
    "garm_list_enterprises", "garm_get_controller_info",
])


async def test_build_server_registers_exactly_spec_tools(CONFIG):
    mcp = build_server(CONFIG)
    names = sorted(t.name for t in await mcp.list_tools())
    assert names == TOOL_NAMES


async def test_deployment_param_is_enum_of_configured_names(CONFIG):
    mcp = build_server(CONFIG)
    for tool in await mcp.list_tools():
        schema = tool.inputSchema["properties"]["deployment"]
        enums = [s["enum"] for s in schema["anyOf"] if "enum" in s]
        assert enums == [["prod", "staging"]], tool.name
        assert "deployment" not in tool.inputSchema.get("required", [])


async def test_list_instances_description_documents_total_and_truncated(CONFIG):
    mcp = build_server(CONFIG)
    tool = next(t for t in await mcp.list_tools() if t.name == "garm_list_instances")
    assert "total" in tool.description and "truncated" in tool.description


@responses.activate
async def test_tool_routes_to_default_and_named_deployment(CONFIG):
    responses.get("https://prod.example.com/api/v1/scalesets", json=[{"id": 1}])
    responses.get("https://staging.example.com/api/v1/scalesets", json=[{"id": 2}, {"id": 3}])
    mcp = build_server(CONFIG)
    default = json.loads((await mcp.call_tool("garm_list_scalesets", {}))[0].text)
    assert default["total"] == 1
    staged = json.loads(
        (await mcp.call_tool("garm_list_scalesets", {"deployment": "staging"}))[0].text
    )
    assert staged["total"] == 2


async def test_unknown_deployment_rejected(CONFIG):
    mcp = build_server(CONFIG)
    with pytest.raises(Exception, match="deployment"):
        await mcp.call_tool("garm_list_scalesets", {"deployment": "nope"})


def test_build_server_expired_token_raises(EXPIRED_CONFIG):
    with pytest.raises(ConfigError, match="expired at"):
        build_server(EXPIRED_CONFIG)


def _sse_or_json(response):
    if response.headers["content-type"].startswith("application/json"):
        return response.json()
    data = [ln[len("data:"):].strip() for ln in response.text.splitlines() if ln.startswith("data:")]
    return json.loads(data[-1])


def test_wrapped_app_accepts_non_loopback_host_end_to_end(CONFIG):
    import dataclasses

    from starlette.testclient import TestClient

    from garm_mcp.auth import require_bearer

    config = dataclasses.replace(CONFIG, bind_address="192.168.122.1:9000")
    mcp = build_server(config)
    app = require_bearer(config.endpoint_token, mcp.streamable_http_app())
    headers = {
        "Authorization": f"Bearer {config.endpoint_token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(app, base_url="http://192.168.122.1:9000") as client:
        init = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}},
        })
        assert init.status_code == 200, init.text
        assert _sse_or_json(init)["result"]["serverInfo"]["name"] == "garm-mcp"
        headers["mcp-session-id"] = init.headers["mcp-session-id"]
        sent = client.post("/mcp", headers=headers,
                           json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert sent.status_code == 202
        listed = client.post("/mcp", headers=headers,
                             json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert listed.status_code == 200, listed.text
        names = sorted(t["name"] for t in _sse_or_json(listed)["result"]["tools"])
        assert names == TOOL_NAMES


@pytest.mark.parametrize("host, extra, expected", [
    ("127.0.0.1:9000", [], 200),
    ("localhost:9000", [], 200),
    ("evil.example.com", [], 421),
    ("vmhost.local:9000", [], 421),
    ("vmhost.local:9000", ["vmhost.local:9000"], 200),
])
def test_host_header_allowlist(CONFIG, host, extra, expected):
    import dataclasses

    from starlette.testclient import TestClient

    config = dataclasses.replace(CONFIG, bind_address="192.168.122.1:9000", allowed_hosts=extra)
    app = build_server(config).streamable_http_app()
    with TestClient(app, base_url=f"http://{host}") as client:
        r = client.post("/mcp", headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                            "clientInfo": {"name": "t", "version": "0"}}})
    assert r.status_code == expected
