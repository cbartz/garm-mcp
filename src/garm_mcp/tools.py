from collections import Counter

from garm_mcp.config import Config, DeploymentConfig
from garm_mcp.garm_client import GarmClient
from garm_mcp.redact import redact

DEFAULT_LIST_LIMIT = 250


def summarize_statuses(instances: list[dict]) -> dict[str, int]:
    return dict(Counter(i.get("status", "unknown") for i in instances))


def truncate_items(items: list, limit: int = DEFAULT_LIST_LIMIT) -> tuple[list, int, bool]:
    return items[:limit], len(items), len(items) > limit


def list_instances_view(
    client: GarmClient,
    scaleset_id: int | None = None,
    status: str | None = None,
) -> dict:
    if scaleset_id is not None:
        instances = client.list_scaleset_instances(scaleset_id)
    else:
        instances = client.list_instances()
    total = len(instances)
    status_counts = summarize_statuses(instances)
    if status is not None:
        instances = [i for i in instances if i.get("status", "unknown") == status]
    page, _, truncated = truncate_items(instances)
    return {
        "instances": redact(page),
        "total": total,
        "truncated": truncated,
        "status_counts": status_counts,
    }


def get_instance_view(client: GarmClient, instance_name: str) -> dict:
    return redact(client.get_instance(instance_name))


def list_view(client: GarmClient, method_name: str) -> dict:
    items = getattr(client, method_name)()
    page, total, truncated = truncate_items(items)
    return {"items": redact(page), "total": total, "truncated": truncated}


def list_jobs_view(client: GarmClient) -> dict:
    items, total = client.list_jobs_page()
    if total is None:
        page, total, truncated = truncate_items(items)
        return {"items": redact(page), "total": total, "truncated": truncated}
    page = redact(items)
    return {"items": page, "total": total, "truncated": total > len(page)}


def get_controller_info_view(client: GarmClient) -> dict:
    return redact(client.get_controller_info())


def resolve_deployment(config: Config, deployment: str | None) -> DeploymentConfig:
    name = config.default_deployment if deployment is None else deployment
    if name not in config.deployments:
        raise ValueError(f"unknown deployment '{name}'; configured: {sorted(config.deployments)}")
    return config.deployments[name]
