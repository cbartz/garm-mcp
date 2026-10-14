# GARM Read-Only MCP Server — Design

Date: 2026-10-14
Status: approved direction (host-side deployment)

## Intent and why

AI agents need to answer troubleshooting and status questions about GARM
deployments — the canonical question being "how many runners are pending,
why, and which scaleset do they belong to". The agent runs inside a VM and
connects to this MCP server; the agent must never be able to mutate GARM.

The agent may have root privileges inside the VM. That rules out every
in-VM credential arrangement: any token stored in the VM is readable by a
root agent. The design therefore removes GARM credentials from the VM
entirely. The MCP server runs on the VM's host, holds the GARM
credentials there, and exposes a curated read-only tool surface over the
network. The VM's only credential is an MCP endpoint token whose leak
buys an attacker nothing beyond what the tools already allow: reads.

Read-only is enforced by where things live and what the server registers,
never by prompt instructions: the server exposes only tools backed by
GARM REST `GET` endpoints, and the agent has no other configured path to
GARM. This is the same enforcement philosophy as `cbartz/restricted-jira-mcp`
(server is the boundary), relocated to the host for the stronger threat
model.

## Goals

- Python MCP server running on the VM's host, using the MCP streamable
  HTTP transport. GARM credentials (per-deployment URL + token) live in
  a host-side config file, never in the VM.
- Talks to the GARM REST API directly with a Bearer token. No dependency
  on `garm-cli`; no shell execution; no generic HTTP passthrough tool.
- Read-only by construction: only tools backed by `GET` endpoints are
  registered. There is no opt-in write mode.
- The MCP endpoint itself requires a token (endpoint auth). This token
  is the only credential that may exist in the VM, and it grants
  read-only GARM access only.
- Multi-deployment: one server serves N GARM deployments from its config.
  Every tool takes an optional `deployment` argument, rendered as a
  JSON-schema enum from the configured deployments. A configurable
  default applies when omitted. MCP calls are stateless per call; no
  session-state "switch deployment" tool.
- Fallback for stdio-only agent harnesses: a tiny, credential-light
  stdio bridge inside the VM (the `mcp-remote` pattern) that forwards
  to the host server over HTTP. The bridge adds no tools and holds only
  the low-stakes endpoint token — never a GARM JWT.

## v1 tool list

All tools accept `deployment?` (enum, optional). Endpoint references are
from `apiserver/swagger.yaml` in the GARM source tree.

| Tool | Backing endpoint | Purpose |
|------|------------------|---------|
| `garm_list_instances` | `GET /instances` (or `GET /scalesets/{id}/instances` when filtered by scaleset) | All runners: status, owning scaleset/pool/entity, plus a summary count by status. Optional `scaleset_id` and `status` filters. |
| `garm_get_instance` | `GET /instances/{instanceName}` | Drill-down: full detail including the `status_messages` log — the "why is it pending" answer. |
| `garm_list_scalesets` | `GET /scalesets` | Scalesets with their entity, pool size, min idle runners. |
| `garm_list_jobs` | `GET /jobs` | Queued/running jobs; complements "why are runners pending" (queue backing up vs pool drained). |
| `garm_list_repositories` | `GET /repositories` | Entity context. |
| `garm_list_organizations` | `GET /organizations` | Entity context. |
| `garm_list_enterprises` | `GET /enterprises` | Entity context. |
| `garm_get_controller_info` | `GET /controller-info` | Cheap liveness/version check. |

Note: `GET /instances` in this GARM version takes no query parameters;
`status` filtering is done client-side by the server, and scaleset
filtering uses the scaleset-scoped endpoint above.

## Threat model

- The agent may run as root inside the VM. Consequence: the VM must
  contain no GARM credentials. The endpoint token is the sole exception
  and is deliberately low-stakes: it only unlocks read-only tool calls.
- An agent that steals the endpoint token gains the ability to read
  GARM through the tools — which it could do anyway. It gains nothing
  else; the server has no write capability to borrow.
- Network exposure of the endpoint is controlled on the host (bind
  address) plus the endpoint token. README documents binding narrowly.
- Secrets in tool responses are still a leak channel: credential,
  endpoint, proxy, metrics-token, and webhook endpoints are excluded
  from v1 entirely, and instance/scaleset/job payloads are audited and
  stripped defensively (the agent must never see a token it could
  replay, e.g. runner registration tokens).
- GARM itself has no read-only token scopes (Bearer JWT with a binary
  `is_admin` flag), so enforcement lives in the server's fixed tool
  surface. Auth uses username/password with automatic re-login
  (recommended) or a static least-privileged token — the credential
  lives only in the host config file either way.
- Residual risks, documented plainly in the README: an attacker with
  host access (not just VM root) can read the server's config; the
  server's own code is the audit surface, kept small by the fixed tool
  list.

## Non-goals

- No write operations of any kind, in any version of this server.
- No garm-cli dependency, no subprocess spawning, no shell.
- No deep analysis/aggregation beyond a light status count; the agent
  reasons over the returned data.
- Not a general-purpose GARM API proxy; the tool list is fixed and
  curated.
- No in-VM credential storage of any kind.

## Constraints and environment

- The server runs on the VM's host, which can reach both GARM
  deployments over the network.
- The VM is Ubuntu 24.04 (noble), PyPI reachable. The agent is expected
  to be able to execute shell commands — including as root — inside the
  VM. The VM needs only: the MCP endpoint URL, the endpoint token, and
  (for stdio-only harnesses) the bridge.
- Deployments run GARM builds close to this source tree (shipped via
  the `canonical/github-runner-operator` charm); minor skew is tolerated
  with lenient JSON parsing, but no per-version fixtures are planned.
- Scale: hundreds of instances per deployment, not thousands.

## Decisions made by the human partner

1. Talk to the GARM REST API directly, not wrap `garm-cli`.
2. Both deployments visible to one server instance; deployment is an
   optional per-call enum argument with a configured default.
3. The v1 tool list was reviewed and accepted ("fine for version 1").
4. Deployments track the latest code closely; version skew is not a
   primary concern.
5. The VM is noble with PyPI access; standard installation is fine.
6. Scale is hundreds of instances per deployment.
7. The agent may have sudoer/root rights in the VM. This settled the
   deployment question: the server runs on the host (streamable HTTP),
   the VM holds no GARM credentials, and a stdio bridge is the fallback
   for stdio-only harnesses. Approved explicitly after the in-VM stdio
   design was shown to be incompatible with a root agent.
8. Username/password re-login pulled into v1 (previously a v2 option)
   after it became clear the charm does not expose
   `jwt_auth.time_to_live`; static-token mode remains supported.

## Decisions made by the designer (marked as my calls)

- **Transport**: MCP streamable HTTP via the official Python MCP SDK;
  configurable bind address on the host; endpoint token via `Authorization`
  header.
- **Endpoint token**: generated at setup, stored in the host config
  file (`0600`); provided to the VM harness config (or bridge) as
  needed. Rotating it revokes VM access without touching GARM.
- **Truncation policy**: list tools cap at 250 items per call;
  status-count summaries are always computed over the full result set
  before truncation, and truncated responses say so explicitly.
  Chosen for the "hundreds" scale; revisit if it grows.
- **Lenient parsing**: unknown JSON fields are ignored, so minor GARM
  version drift does not break the server.
- **TLS**: every deployment gets an optional CA-bundle knob in the
  host config, harmless if the certs verify from the host's default
  trust store.
- **Auth modes (v1)**: per deployment, either username/password (the
  server mints its own JWT via `POST /api/v1/auth/login` and
  transparently re-logins once on a 401 — recommended, and required for
  charm-managed GARM where `jwt_auth.time_to_live` is not exposed as a
  charm option) or a static long-lived JWT (the server checks its `exp`
  at startup and fails with a pointed error; README instructs raising
  the controller-wide `jwt_auth.time_to_live` rather than hand-signing
  with the JWT secret). Both live in the host config file only.
- **Project layout**: standalone project directory (`garm-mcp`,
  sibling of the garm checkout): `pyproject.toml`, `src/garm_mcp/`,
  `tests/`, `scripts/` smoke tests, `uv` for dependency management.

## What is left to the builder

- Exact host config-file format (deployments, endpoint token, bind
  address, CA bundles) and its validation.
- Endpoint auth mechanics with the chosen MCP SDK (header extraction,
  401 handling) and how remote MCP harness configs or the bridge pass
  the token.
- The defensive secret audit of Instance/Scaleset/Job/entity response
  models, and any field trimming that follows from it.
- Truncation mechanics for the 250-item cap (which fields survive, how
  truncation is surfaced to the agent).
- The stdio bridge, if implemented in v1: minimal stdio↔HTTP forwarder
  with token injection; document when it is and is not needed.
- Test and smoke-test structure following the `restricted-jira-mcp`
  patterns (offline tests against canned JSON; smoke script against a
  real deployment when credentials are present).
- README covering: host setup (config file, bind address, endpoint
  token), VM harness setup (remote MCP config or bridge), GARM
  authentication setup (username/password with auto re-login, or static
  token with TTL guidance), and the threat-model summary above.

## References

- `cbartz/restricted-jira-mcp` — enforcement philosophy and
  project/test/smoke layout to follow.
- GARM source tree (this repo): `apiserver/swagger.yaml` (endpoint and
  model reference), `client/` (Go client showing exact calls per
  endpoint), `auth/` (Bearer JWT, `is_admin` binary flag).
- MCP streamable HTTP transport specification; the `mcp-remote` pattern
  for stdio↔HTTP bridging.
- Instance lifecycle statuses: `pending_create`, `creating`, `running`,
  `error`, `pending_delete`, `pending_force_delete`, `deleting`,
  `deleted`, `stopped`, `unknown`.