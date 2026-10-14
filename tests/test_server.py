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
