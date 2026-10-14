from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from garm_mcp.config import Config, check_token_expiry
from garm_mcp.garm_client import GarmClient
from garm_mcp.tools import (
    get_controller_info_view,
    get_instance_view,
    list_instances_view,
    list_view,
    resolve_deployment,
)


def _transport_security(config: Config) -> TransportSecuritySettings:
    bind_host = config.bind_address.rsplit(":", 1)[0]
    allowed = [
        config.bind_address,
        f"{bind_host}:*",
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        *config.allowed_hosts,
    ]
    allowed = list(dict.fromkeys(allowed))
    origins = [f"{scheme}://{entry}" for entry in allowed for scheme in ("http", "https")]
    origins.extend(config.allowed_origins)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed,
        allowed_origins=list(dict.fromkeys(origins)),
    )


def build_server(config: Config) -> FastMCP:
    clients: dict[str, GarmClient] = {}
    for name, dep in config.deployments.items():
        if dep.token:
            check_token_expiry(name, dep.token)
        clients[name] = GarmClient(
            name=name,
            base_url=dep.base_url,
            token=dep.token,
            username=dep.username,
            password=dep.password,
            ca_bundle=dep.ca_bundle,
        )

    Deployment = Literal[tuple(sorted(config.deployments))]  # type: ignore[valid-type]
    mcp = FastMCP("garm-mcp", transport_security=_transport_security(config))

    def client_for(deployment: str | None) -> GarmClient:
        return clients[resolve_deployment(config, deployment).name]

    @mcp.tool(
        name="garm_list_instances",
        description=(
            "List GARM runner instances, optionally filtered by scale set id and/or status. "
            "Returns {instances, total, truncated, status_counts}. 'total' is the full number "
            "of instances fetched from GARM before any status filtering or truncation; "
            "'truncated' is true when the returned 'instances' list was cut to the first 250 "
            "entries. 'status_counts' covers the full fetched set, not just the filtered page. "
            "'deployment' selects the GARM deployment (default: the configured default)."
        ),
    )
    def garm_list_instances(
        scaleset_id: int | None = None,
        status: str | None = None,
        deployment: Deployment | None = None,
    ) -> dict:
        return list_instances_view(client_for(deployment), scaleset_id, status)

    @mcp.tool(
        name="garm_get_instance",
        description=(
            "Get one GARM runner instance by name. Secret-looking fields are redacted. "
            "'deployment' selects the GARM deployment (default: the configured default)."
        ),
    )
    def garm_get_instance(instance_name: str, deployment: Deployment | None = None) -> dict:
        return get_instance_view(client_for(deployment), instance_name)

    def register_list_tool(tool_name: str, method_name: str, noun: str) -> None:
        def tool(deployment: Deployment | None = None) -> dict:
            return list_view(client_for(deployment), method_name)

        tool.__name__ = tool_name
        mcp.tool(
            name=tool_name,
            description=(
                f"List GARM {noun}. Returns {{items, total, truncated}}: 'total' is the full "
                "count fetched; 'truncated' is true when 'items' was cut to the first 250. "
                "'deployment' selects the GARM deployment (default: the configured default)."
            ),
        )(tool)

    register_list_tool("garm_list_scalesets", "list_scalesets", "scale sets")
    register_list_tool("garm_list_jobs", "list_jobs", "workflow jobs")
    register_list_tool("garm_list_repositories", "list_repositories", "repositories")
    register_list_tool("garm_list_organizations", "list_organizations", "organizations")
    register_list_tool("garm_list_enterprises", "list_enterprises", "enterprises")

    @mcp.tool(
        name="garm_get_controller_info",
        description=(
            "Get GARM controller info (controller id, URLs, version). "
            "'deployment' selects the GARM deployment (default: the configured default)."
        ),
    )
    def garm_get_controller_info(deployment: Deployment | None = None) -> dict:
        return get_controller_info_view(client_for(deployment))

    return mcp
