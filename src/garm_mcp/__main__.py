import argparse

import uvicorn

from garm_mcp.auth import require_bearer
from garm_mcp.config import load_config
from garm_mcp.server import build_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="garm-mcp", description="Read-only GARM MCP server")
    parser.add_argument("--config", default=None, help="path to config file (default: $GARM_MCP_CONFIG)")
    args = parser.parse_args()

    config = load_config(args.config)
    mcp = build_server(config)
    app = require_bearer(config.endpoint_token, mcp.streamable_http_app())
    host, port = config.bind_address.rsplit(":", 1)
    uvicorn.run(app, host=host, port=int(port))


if __name__ == "__main__":
    main()
