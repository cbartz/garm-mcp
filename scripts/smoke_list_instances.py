"""Smoke check: load the config, list instances on the default deployment, print status counts."""
import argparse
import json
import sys

from garm_mcp.config import load_config
from garm_mcp.errors import ConfigError, GarmApiError
from garm_mcp.garm_client import GarmClient
from garm_mcp.tools import summarize_statuses


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List instances on the default GARM deployment and print status counts as JSON. "
            "Reads the config path from $GARM_MCP_CONFIG."
        )
    )
    parser.parse_args()

    try:
        config = load_config()
        dep = config.deployments[config.default_deployment]
        client = GarmClient(
            name=dep.name,
            base_url=dep.base_url,
            token=dep.token,
            username=dep.username,
            password=dep.password,
            ca_bundle=dep.ca_bundle,
        )
        instances = client.list_instances()
    except (GarmApiError, ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(summarize_statuses(instances), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
