# garm-mcp

A read-only [MCP](https://modelcontextprotocol.io) server that lets AI agents inspect one or more [GARM](https://github.com/cloudbase/garm) (GitHub Actions Runner Manager) deployments. It exposes a small, fixed set of read tools over streamable HTTP, protected by a bearer token.

## 1. What it is

The server runs on a **host** you control, holds the GARM credentials, and serves read-only tools to an AI agent that typically runs in a separate **VM**.

Threat model in short:

- **The VM holds only the endpoint token.** That token unlocks read-only tool calls and nothing else. If it leaks, the attacker can read GARM through the tools and can do nothing more, because the server has no write capability to borrow.
- **GARM credentials never enter the VM.** Usernames, passwords and GARM JWTs live only in the host config file.
- **The agent may be root in the VM and still cannot mutate GARM.** GARM itself has no read-only token scopes (its JWT has a binary `is_admin` flag), so enforcement lives in this server's fixed tool surface: there are no write tools, and none are planned.
- **Secrets in responses are a leak channel too.** Credential, endpoint, proxy, webhook and metrics-token endpoints are not exposed at all. Instance, scale set and job payloads are defensively stripped of secret-looking fields.
- **Residual risk:** anyone with access to the *host* (not just root in the VM) can read the config file and the GARM credentials in it. The server's own code is the audit surface, kept small by the fixed tool list.

## 2. Host setup

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Create the config from the example and lock down its permissions. It contains GARM credentials and the endpoint token:

   ```bash
   sudo mkdir -p /etc/garm-mcp
   sudo cp config.example.json /etc/garm-mcp/config.json
   sudo chmod 600 /etc/garm-mcp/config.json
   ```

   Edit it: set `bind_address`, `default_deployment`, and one entry per GARM deployment under `deployments` (see [GARM authentication](#4-garm-authentication)). Each deployment has `base_url`, optional `ca_bundle` (path to a CA file for a private/self-signed GARM certificate), and exactly one auth mode: `username` + `password`, or `token`.

   Make the file readable by the user that runs the server (for example `chown` it to that user).

3. Generate the endpoint token and put it in `endpoint_token`:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

4. Run the server:

   ```bash
   uv run python -m garm_mcp --config /etc/garm-mcp/config.json
   ```

   `--config` defaults to the `GARM_MCP_CONFIG` environment variable. The MCP endpoint is served at `http://<bind_address>/mcp`.

**Bind address.** `bind_address` is a plain `host:port` (default in the example: `127.0.0.1:9000`). Bind to an interface that only the agent VM can reach (for example a host-only/bridge address), never `0.0.0.0` on a network others can reach. The bearer token is the second layer, not the first. The server speaks plain HTTP; if traffic crosses a network you do not trust, terminate TLS in a reverse proxy in front of it.

**Startup failures are loud.** A bad or missing config (`ConfigError`), or an expired static token, makes the process exit with a Python traceback whose last line is the error message. There is no friendlier wrapper in v1; check the last line.

## 3. Agent/VM setup

Give the agent harness the server URL and the endpoint token. For a harness that supports remote (streamable HTTP) MCP servers, the config looks like this (exact key names vary by harness):

```json
{
  "mcpServers": {
    "garm": {
      "type": "http",
      "url": "http://<host-address>:9000/mcp",
      "headers": {
        "Authorization": "Bearer <endpoint token>"
      }
    }
  }
}
```

**Fallback for stdio-only harnesses (v1).** If the harness can only launch stdio MCP servers, use `mcp-remote` as the bridge. This is a documented fallback only; v1 ships no custom bridge:

```bash
npx mcp-remote http://<host-address>:9000/mcp --header "Authorization: Bearer <endpoint token>"
```

The VM needs only the URL and the endpoint token. Do not copy any GARM credential into it.

## 4. GARM authentication

Each deployment in the host config authenticates to GARM in exactly one of two ways. Setting both, or neither, is a config error.

### Recommended: username + password

```json
"prod": {"base_url": "https://garm-prod.example.com", "username": "admin", "password": "...", "ca_bundle": null}
```

The server logs in to GARM itself (`POST /api/v1/auth/login`), mints its own JWT, and logs in again transparently when GARM answers 401 because the JWT expired. This works unchanged on charm-managed GARM, which does not expose `jwt_auth.time_to_live`.

### Alternative: static token

```json
"staging": {"base_url": "https://garm-stg.example.com", "token": "eyJ...", "ca_bundle": null}
```

A static token never refreshes, so it must stay valid for as long as you want the server to run, and GARM's default JWT lifetime is limited. To lengthen it, raise `time_to_live` in the `[jwt_auth]` section of the GARM controller config, for example:

```toml
[jwt_auth]
time_to_live = "8760h"
```

This is controller-wide (it applies to every token GARM issues), and GARM must be restarted for it to take effect.

Ways to get a token:

- Copy `base_url` and `bearer_token` for each manager profile from `~/.local/share/garm-cli/config.toml`.
- Mint a fresh one with `POST /api/v1/auth/login` (body `{"username": ..., "password": ...}`; the response contains `token`).

Never hand-sign a JWT with GARM's `jwt_secret`.

### What an expired token looks like

Static tokens are checked at startup. The `exp` claim is read (without verifying the signature) and an expired token aborts startup with a traceback ending in:

```
garm_mcp.errors.ConfigError: GARM token for deployment 'staging' expired at 2026-01-01T00:00:00+00:00; mint a longer-lived JWT (see README)
```

A token with no `exp` claim, or one that cannot be parsed, is also refused at startup. A token that expires while the server is running surfaces as a GARM API error from the tool calls, so restart with a new token (or switch that deployment to username + password).

## 5. Tools

Every tool is read-only. Each takes an optional `deployment` argument: an enum of the configured deployment names. If omitted, the `default_deployment` from the config is used.

| Tool | Purpose |
| --- | --- |
| `garm_list_instances` | List runner instances, optionally filtered by `scaleset_id` and/or `status`. Returns `{instances, total, truncated, status_counts}`; `status_counts` covers the full fetched set. |
| `garm_get_instance` | Get one runner instance by `instance_name`. |
| `garm_list_scalesets` | List scale sets. |
| `garm_list_jobs` | List workflow jobs. |
| `garm_list_repositories` | List repositories registered in GARM. |
| `garm_list_organizations` | List organizations registered in GARM. |
| `garm_list_enterprises` | List enterprises registered in GARM. |
| `garm_get_controller_info` | Get controller info (controller id, URLs, version). |

List results are truncated to the first 250 entries; `truncated` is `true` when that happened and `total` is the full count fetched. Secret-looking fields in responses are redacted.

## 6. Smoke validation

`scripts/smoke_list_instances.py` checks connectivity and credentials without involving MCP. It loads the config, lists instances on the default deployment, and prints the status counts as JSON. On a config or GARM API error it prints the message to stderr and exits 1.

```bash
GARM_MCP_CONFIG=/etc/garm-mcp/config.json uv run python scripts/smoke_list_instances.py
```

Example output:

```json
{
  "idle": 3,
  "running": 2
}
```

## Development

```bash
uv sync
uv run pytest -v
```
