from collections import Counter

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
