# GARM Read-Only MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A host-side Python MCP server (streamable HTTP) exposing 8 read-only GARM tools over the GARM REST API, multi-deployment aware, with no GARM credentials ever living in the agent's VM.

**Architecture:** Official Python MCP SDK (`mcp`) serving streamable HTTP from the VM host; a thin `requests`-based client per deployment; tool logic as pure, testable view functions (redact → truncate → counts); bearer-token middleware guarding the MCP endpoint. Project layout follows `cbartz/restricted-jira-mcp`.

**Tech Stack:** Python >=3.10, `mcp` SDK (streamable HTTP + FastMCP), `requests`, `uv`; tests with `pytest` + `responses`; deployed via a `0600` JSON config file on the host.

**Spec:** `docs/superpowers/specs/2026-10-14-garm-readonly-mcp-design.md`

## Global Constraints

- Read-only by construction: only `GET`-backed tools registered; no write mode, no opt-in writes.
- No `garm-cli`, no subprocess/shell execution, no generic HTTP passthrough tool.
- Exact tool names: `garm_list_instances`, `garm_get_instance`, `garm_list_scalesets`, `garm_list_jobs`, `garm_list_repositories`, `garm_list_organizations`, `garm_list_enterprises`, `garm_get_controller_info`.
- Every tool accepts optional `deployment` (enum from configured deployments); omitted → `default_deployment`; unknown name → clear error listing configured names; stateless per call.
- GARM API base path is `{base_url}/api/v1` (Go client `DefaultBasePath`); all calls `GET` with `Authorization: Bearer <token>` and `Accept: application/json`.
- Two auth modes per deployment: (a) username/password — the server mints its own JWT via `POST /api/v1/auth/login` (response body `{"token": "..."}`), caches it, and transparently re-logins exactly once on a 401 before failing loudly; (b) static token — used as-is; its `exp` is checked at startup with a pointed error naming the deployment.
- HTTP timeout 10.0s; on `ConnectionError`/`Timeout`/HTTP 502/503/504 retry exactly once, then fail with a clear error mapped from the JSON `APIErrorResponse`.
- Truncation cap 250 items per list call; status-count summaries computed over the full fetched set (before status filtering and before truncation); truncated responses must say so explicitly.
- Lenient parsing: unknown JSON fields are ignored, never fatal.
- Credential handling: GARM tokens live only in the host config file (`0600`); the endpoint token is the only credential that may exist in the VM; never log or commit tokens.
- Server checks each static GARM JWT's `exp` at startup and fails with a pointed error naming the deployment.
- Package `garm_mcp` under `src/`, managed with `uv`; Python `>=3.10`.

## Review Focus

1. Expired or unparseable GARM JWT at startup → server must refuse to start with an error naming the deployment and pointing at the README remedy (pinned by Task 1 `test_expired_token_raises` / `test_unparseable_token_raises`, Task 5 `test_build_server_expired_token_raises`).
2. Secret-looking fields in GARM responses (`registration_token`, `secret`, …) reaching agent output → must be `[REDACTED]` in every tool response (pinned by Task 3 `test_redact_*`).
3. `deployment` argument naming an unconfigured deployment → clear error listing configured names, never a silent fallback to default (pinned by Task 5 `test_resolve_deployment_unknown_raises`).
4. Truncated list responses → `status_counts` still computed over the complete fetched set, not the truncated page (pinned by Task 4 `test_list_instances_truncation_counts_full_set`).
5. Missing or wrong MCP endpoint token → HTTP 401 before any tool logic runs (pinned by Task 6 `test_require_bearer_*`).

---

### Task 1: Project scaffold + config module

**Files:**
- Create: `pyproject.toml`, `src/garm_mcp/__init__.py`, `src/garm_mcp/errors.py`, `src/garm_mcp/config.py`, `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `errors.ConfigError(Exception)`
  - `config.DeploymentConfig` dataclass: `name: str`, `base_url: str`, `token: str | None = None`, `username: str | None = None`, `password: str | None = None`, `ca_bundle: str | None = None`
  - `config.Config` dataclass: `bind_address: str`, `endpoint_token: str`, `deployments: dict[str, DeploymentConfig]`, `default_deployment: str`
  - `config.load_config(path: str | None = None) -> Config` — `path` arg falls back to env `GARM_MCP_CONFIG`; raises `ConfigError` if neither.
  - `config.check_token_expiry(name: str, token: str, now: float | None = None) -> None` — raises `ConfigError` on expired/missing-`exp`/unparseable tokens.
  - `tests.make_jwt(payload: dict) -> str` helper (unsigned `header.payload` base64url construction) in `tests/conftest.py` — reused by later tasks.

Config file format (pinned, used by `config.example.json` in Task 7):

```json
{
  "bind_address": "127.0.0.1:9000",
  "endpoint_token": "CHANGE_ME",
  "default_deployment": "prod",
  "deployments": {
    "prod": {"base_url": "https://garm-prod.example.com", "username": "admin", "password": "CHANGE_ME", "ca_bundle": null},
    "staging": {"base_url": "https://garm-stg.example.com", "token": "eyJ...", "ca_bundle": null}
  }
}
```

Validation rule per deployment: either `token`, or `username` and `password` — never both, never neither. Error message: `deployment '<name>' needs either 'token' or 'username' and 'password'` / `deployment '<name>' must not set both 'token' and username/password`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
import time, pytest
from garm_mcp.config import load_config, check_token_expiry
from garm_mcp.errors import ConfigError

VALID = {"bind_address": "127.0.0.1:9000", "endpoint_token": "ep-token",
         "default_deployment": "prod",
         "deployments": {"prod": {"base_url": "https://p.example.com", "token": "t1", "ca_bundle": None},
                         "staging": {"base_url": "https://s.example.com", "token": "t2", "ca_bundle": None}}}

def test_load_config_valid(tmp_path):
    p = tmp_path / "config.json"; p.write_text(json.dumps(VALID))
    cfg = load_config(str(p))
    assert cfg.bind_address == "127.0.0.1:9000" and cfg.endpoint_token == "ep-token"
    assert set(cfg.deployments) == {"prod", "staging"}
    assert cfg.deployments["prod"].base_url == "https://p.example.com"

def test_load_config_missing_file_raises():
    with pytest.raises(ConfigError, match="config file not found"):
        load_config("/nonexistent/config.json")

def test_load_config_missing_key_raises(tmp_path):
    p = tmp_path / "c.json"; p.write_text('{"bind_address": "h:1"}')
    with pytest.raises(ConfigError, match="missing required config key: endpoint_token"):
        load_config(str(p))

def test_load_config_bad_default_raises(tmp_path):
    bad = dict(VALID, default_deployment="nope")
    p = tmp_path / "c.json"; p.write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match="default_deployment 'nope' is not a configured deployment"):
        load_config(str(p))

def test_expired_token_raises():
    payload = {"exp": int(time.time()) - 10}
    with pytest.raises(ConfigError, match="expired at"):
        check_token_expiry("prod", make_jwt(payload))

def test_token_without_exp_raises():
    with pytest.raises(ConfigError, match="no exp claim"):
        check_token_expiry("prod", make_jwt({"sub": "x"}))

def test_unparseable_token_raises():
    with pytest.raises(ConfigError, match="cannot parse GARM token for deployment 'prod'"):
        check_token_expiry("prod", "not-a-jwt")

def test_deployment_without_auth_raises(tmp_path):
    bad = dict(VALID)
    bad["deployments"] = {"prod": {"base_url": "https://p.example.com", "ca_bundle": None}}
    p = tmp_path / "c.json"; p.write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match="needs either 'token' or 'username' and 'password'"):
        load_config(str(p))

def test_deployment_with_both_auth_modes_raises(tmp_path):
    bad = dict(VALID)
    bad["deployments"] = {"prod": {"base_url": "https://p.example.com", "token": "t",
                                   "username": "u", "password": "p", "ca_bundle": None}}
    p = tmp_path / "c.json"; p.write_text(json.dumps(bad))
    with pytest.raises(ConfigError, match="must not set both"):
        load_config(str(p))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/ubuntu/work/garm-mcp && uv run pytest tests/test_config.py -v`
Expected: FAIL (modules do not exist)

- [ ] **Step 3: Implement `errors.py` and `config.py`**

`errors.py`: `class ConfigError(Exception)`. `config.py`: the two dataclasses and two functions; `load_config` validates with error messages exactly matching the test regexes (`missing required config key: <key>`, `config file not found: <path>`, `default_deployment '<x>' is not a configured deployment`, the per-deployment auth rules quoted above); `check_token_expiry` base64url-decodes `token.split(".")[1]`, reads `exp` (missing → `no exp claim; refusing token`), compares against `now or time.time()` (expired → `GARM token for deployment '<name>' expired at <iso8601>; mint a longer-lived JWT (see README)`), unparseable → `cannot parse GARM token for deployment '<name>'`. `pyproject.toml`: hatchling, `requires-python = ">=3.10"`, deps `mcp>=1.9`, `requests>=2.31`; dev deps `pytest`, `pytest-asyncio`, `responses`; `[tool.pytest.ini_options] asyncio_mode = "auto"`; packages from `src`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/garm_mcp tests/
git commit -m "feat: project scaffold and validated config module"
```

---

### Task 2: GARM API client

**Files:**
- Modify: `src/garm_mcp/errors.py` (add `GarmApiError`)
- Create: `src/garm_mcp/garm_client.py`
- Test: `tests/test_garm_client.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `errors.GarmApiError(Exception)` with `__init__(self, message: str, status_code: int | None = None)`
  - `garm_client.GarmClient(name: str, base_url: str, token: str | None = None, username: str | None = None, password: str | None = None, ca_bundle: str | None = None, timeout: float = 10.0, retry_delay: float = 0.5)` with methods:
    - `login() -> str` — `POST {base_url}/api/v1/auth/login` with JSON `{"username": ..., "password": ...}`; stores and returns the `token` field of the response.
    - Data methods (all return parsed JSON; `list_*` return lists, `get_*` return dicts):
      `list_instances()`, `get_instance(instance_name: str)`, `list_scalesets()`, `list_scaleset_instances(scaleset_id: int)`, `list_jobs()`, `list_repositories()`, `list_organizations()`, `list_enterprises()`, `get_controller_info()`

Behavior pinned: every call is `GET {base_url}/api/v1/<path>` with `Authorization: Bearer <token>` and `Accept: application/json`; when no static token is configured but username/password are, the first call triggers a lazy `login()`; on a 401 response with username/password configured, re-login exactly once and retry the original request once — a second 401 (or a 401 with no credentials) raises `GarmApiError`; on `requests.ConnectionError`, `requests.Timeout`, or HTTP 502/503/504 retry exactly once after `retry_delay`, then raise `GarmApiError`; other non-2xx → `GarmApiError(message=body["error"]["message"], status_code=code)` (unparseable body → `unknown error (HTTP <code>)`); `ca_bundle` sets `session.verify`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_garm_client.py
import requests, responses
from garm_mcp.garm_client import GarmClient
from garm_mcp.errors import GarmApiError

BASE = "https://garm.example.com"
CLIENT = dict(name="prod", base_url=BASE, token="jwt1")

@responses.activate
def test_list_instances_sends_bearer_and_parses():
    responses.get(f"{BASE}/api/v1/instances", json=[{"name": "i1"}, {"name": "i2"}])
    out = GarmClient(**CLIENT).list_instances()
    assert [i["name"] for i in out] == ["i1", "i2"]
    assert responses.calls[0].request.headers["Authorization"] == "Bearer jwt1"

@responses.activate
def test_get_instance_uses_encoded_name_path():
    responses.get(f"{BASE}/api/v1/instances/runner-abc", json={"name": "runner-abc"})
    assert GarmClient(**CLIENT).get_instance("runner-abc")["name"] == "runner-abc"

@responses.activate
def test_scaleset_instances_url():
    responses.get(f"{BASE}/api/v1/scalesets/42/instances", json=[])
    assert GarmClient(**CLIENT).list_scaleset_instances(42) == []

@responses.activate
def test_api_error_maps_message_and_status():
    responses.get(f"{BASE}/api/v1/jobs", json={"error": {"message": "pool not found", "type": 0}}, status=400)
    with pytest.raises(GarmApiError, match="pool not found") as ei:
        GarmClient(**CLIENT).list_jobs()
    assert ei.value.status_code == 400

@responses.activate
def test_transient_failure_retries_once():
    responses.get(f"{BASE}/api/v1/instances", body=requests.ConnectionError())
    responses.get(f"{BASE}/api/v1/instances", json=[])
    client = GarmClient(**CLIENT, retry_delay=0.01)
    assert client.list_instances() == []
    assert len(responses.calls) == 2

@responses.activate
def test_non_json_error_falls_back():
    responses.get(f"{BASE}/api/v1/instances", body="boom", status=502)
    responses.get(f"{BASE}/api/v1/instances", body="boom", status=502)
    with pytest.raises(GarmApiError, match="unknown error \\(HTTP 502\\)"):
        GarmClient(**CLIENT, retry_delay=0.01).list_instances()

def test_ca_bundle_sets_verify():
    c = GarmClient(**CLIENT, ca_bundle="/etc/ca.pem")
    assert c.session.verify == "/etc/ca.pem"

@responses.activate
def test_login_mints_and_caches_token():
    responses.post(f"{BASE}/api/v1/auth/login", json={"token": "fresh"})
    responses.get(f"{BASE}/api/v1/instances", json=[])
    client = GarmClient(name="prod", base_url=BASE, username="admin", password="pw")
    assert client.list_instances() == []
    assert responses.calls[1].request.headers["Authorization"] == "Bearer fresh"

@responses.activate
def test_401_triggers_relogin_once():
    responses.get(f"{BASE}/api/v1/instances", json={"error": {"message": "expired", "type": 0}}, status=401)
    responses.post(f"{BASE}/api/v1/auth/login", json={"token": "fresh2"})
    responses.get(f"{BASE}/api/v1/instances", json=[{"name": "i1"}])
    client = GarmClient(name="prod", base_url=BASE, token="stale", username="admin", password="pw")
    assert [i["name"] for i in client.list_instances()] == ["i1"]
    assert responses.calls[2].request.headers["Authorization"] == "Bearer fresh2"

@responses.activate
def test_401_without_credentials_raises():
    responses.get(f"{BASE}/api/v1/instances", json={"error": {"message": "unauthorized", "type": 0}}, status=401)
    with pytest.raises(GarmApiError, match="unauthorized"):
        GarmClient(**CLIENT).list_instances()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_garm_client.py -v`
Expected: FAIL (module does not exist)

- [ ] **Step 3: Implement `GarmClient`**

One `requests.Session` per client; a private `_get(path: str)` helper does the auth flow (lazy `login()`, 401 re-login-once) plus fetch/retry/error-mapping; the nine public data methods are one-liners mapping to paths: `instances`, `instances/{name}`, `scalesets`, `scalesets/{id}/instances`, `jobs`, `repositories`, `organizations`, `enterprises`, `controller-info`. `login()` posts username/password JSON and stores the returned token on the instance.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_garm_client.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/garm_mcp tests/
git commit -m "feat: GARM API client with login, re-login on 401, retries, error mapping"
```

---

### Task 3: Response redaction

**Files:**
- Create: `src/garm_mcp/redact.py`
- Test: `tests/test_redact.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `redact.redact(obj: Any) -> Any` — recursively walks dicts/lists; any dict key matching `REDACTED_KEY = re.compile(r"(?i)token|secret|password|credential")` has its value replaced with the string `"[REDACTED]"`; all other structure and values pass through unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_redact.py
from garm_mcp.redact import redact

def test_redacts_registration_token():
    out = redact({"name": "runner-1", "registration_token": "ABC123", "status": "pending_create"})
    assert out["registration_token"] == "[REDACTED]"
    assert out["name"] == "runner-1" and out["status"] == "pending_create"

def test_redacts_nested_and_in_lists():
    data = [{"status_messages": [{"message": "creating"}], "provider": {"secret": "s", "token": "t"}},
            {"oauth_token": "x"}]
    out = redact(data)
    assert out[0]["status_messages"][0]["message"] == "creating"
    assert out[0]["provider"] == {"secret": "[REDACTED]", "token": "[REDACTED]"}
    assert out[1]["oauth_token"] == "[REDACTED]"

def test_non_dict_payloads_pass_through():
    assert redact({"count": 5, "flag": True, "note": None}) == {"count": 5, "flag": True, "note": None}
    assert redact("plain") == "plain"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_redact.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `redact`**

Plain recursion (dict → walk items, list → map, else return); no third-party helpers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_redact.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/garm_mcp/redact.py tests/test_redact.py
git commit -m "feat: defensive redaction of secret-looking response fields"
```

---

### Task 4: Instance tool logic (filters, truncation, status counts)

**Files:**
- Create: `src/garm_mcp/tools.py`
- Test: `tests/test_tools_instances.py`

**Interfaces:**
- Consumes: `GarmClient` (Task 2), `redact` (Task 3).
- Produces:
  - `tools.DEFAULT_LIST_LIMIT = 250`
  - `tools.summarize_statuses(instances: list[dict]) -> dict[str, int]` — counts by the `"status"` key; instances without it land in `"unknown"`.
  - `tools.truncate_items(items: list, limit: int = DEFAULT_LIST_LIMIT) -> tuple[list, int, bool]` → `(page, total, truncated)`
  - `tools.list_instances_view(client: GarmClient, scaleset_id: int | None = None, status: str | None = None) -> dict` returning `{"instances": [...redacted page...], "total": int, "truncated": bool, "status_counts": {...}}`

Behavior pinned: `scaleset_id` given → `client.list_scaleset_instances(scaleset_id)`, else `client.list_instances()`; `status_counts` computed over the complete fetched set (before status filtering and before truncation); `status` filter applied client-side as exact match (missing-status instances match `"unknown"`); returned page is redacted and truncated to the limit.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tools_instances.py
import responses
from garm_mcp.tools import list_instances_view, summarize_statuses, truncate_items

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
```

(`CLIENT` is a module-level `GarmClient(name="prod", base_url="https://g.example.com", token="t")`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools_instances.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the three functions**

`summarize_statuses`: one `Counter` pass. `truncate_items`: slice + flag. `list_instances_view`: fetch → counts on full set → status filter → truncate → redact page → assemble dict. Note the exact key order of operations: counts happen before filtering and truncation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools_instances.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/garm_mcp/tools.py tests/test_tools_instances.py
git commit -m "feat: instance listing with filters, truncation, full-set status counts"
```

---

### Task 5: Remaining tool views + server assembly

**Files:**
- Modify: `src/garm_mcp/tools.py` (add views + `resolve_deployment`), `tests/conftest.py` (add `CONFIG` / `EXPIRED_CONFIG` fixtures)
- Create: `src/garm_mcp/server.py`
- Test: `tests/test_tools_views.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: `Config`, `DeploymentConfig`, `check_token_expiry` (Task 1), `GarmClient` (Task 2), `redact` (Task 3), `list_instances_view`/`summarize_statuses`/`truncate_items` (Task 4).
- Produces:
  - `tools.get_instance_view(client: GarmClient, instance_name: str) -> dict` — redacted pass-through.
  - `tools.list_view(client: GarmClient, method_name: str) -> dict` — calls `getattr(client, method_name)()`; returns `{"items": [...redacted page...], "total": int, "truncated": bool}`.
  - `tools.get_controller_info_view(client: GarmClient) -> dict` — redacted pass-through (no truncation).
  - `tools.resolve_deployment(config: Config, deployment: str | None) -> DeploymentConfig` — `None` → config's default; unknown name → `ValueError("unknown deployment '<name>'; configured: ['prod', ...]")` (sorted names).
  - `server.build_server(config: Config) -> FastMCP` — calls `check_token_expiry` for every deployment configured with a static token (raises `ConfigError` at startup), builds one `GarmClient` per deployment, registers exactly the 8 spec tool names; each tool is a sync function whose params are the view-function params plus `deployment: Literal[<sorted configured names>] | None = None` (dynamic `Literal[tuple(sorted(config.deployments))]`), resolving via `resolve_deployment` before calling the view.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tools_views.py
import responses
from garm_mcp.tools import get_instance_view, list_view, get_controller_info_view, resolve_deployment

@responses.activate
def test_list_view_shape_and_redaction():
    responses.get("https://g.example.com/api/v1/scalesets",
                  json=[{"id": 1, "github_token": "T"}])
    out = list_view(CLIENT, "list_scalesets")
    assert out["total"] == 1 and out["truncated"] is False
    assert out["items"][0]["github_token"] == "[REDACTED]"

@responses.activate
def test_get_instance_view_redacts():
    responses.get("https://g.example.com/api/v1/instances/r1", json={"name": "r1", "registration_token": "T"})
    assert get_instance_view(CLIENT, "r1")["registration_token"] == "[REDACTED]"

def test_resolve_deployment_default_and_unknown(CONFIG):
    assert resolve_deployment(CONFIG, None).name == "prod"
    assert resolve_deployment(CONFIG, "staging").name == "staging"
    with pytest.raises(ValueError, match="unknown deployment 'nope'; configured:"):
        resolve_deployment(CONFIG, "nope")
```

```python
# tests/test_server.py
from garm_mcp.server import build_server

async def test_build_server_registers_exactly_spec_tools(CONFIG):
    mcp = build_server(CONFIG)
    names = sorted(t.name for t in await mcp.list_tools())
    assert names == sorted(["garm_list_instances", "garm_get_instance", "garm_list_scalesets",
                            "garm_list_jobs", "garm_list_repositories", "garm_list_organizations",
                            "garm_list_enterprises", "garm_get_controller_info"])

def test_build_server_expired_token_raises(EXPIRED_CONFIG):
    with pytest.raises(ConfigError, match="expired at"):
        build_server(EXPIRED_CONFIG)
```

(`CONFIG`/`EXPIRED_CONFIG` come from `tests/conftest.py` fixtures built on Task 1's `make_jwt` with `exp = int(time.time()) + 3600` / `exp = int(time.time()) - 10`; tokens constructed so Task 1's expiry check passes/fails.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools_views.py tests/test_server.py -v`
Expected: FAIL

- [ ] **Step 3: Implement views, `resolve_deployment`, `build_server`**

Views are thin (fetch/redact/assemble). `build_server` creates the FastMCP instance, defines the 8 sync tool functions (closing over the per-deployment client dict and `resolve_deployment`), and registers them with docstrings describing purpose + the `deployment` enum. Each tool's return value is its view function's dict.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools_views.py tests/test_server.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/garm_mcp tests/
git commit -m "feat: all 8 read-only tools registered with deployment routing"
```

---

### Task 6: Endpoint auth + HTTP entrypoint

**Files:**
- Create: `src/garm_mcp/auth.py`, `src/garm_mcp/__main__.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `load_config`, `build_server` (Tasks 1, 5).
- Produces:
  - `auth.require_bearer(expected_token: str, app: ASGIApp) -> ASGIApp` — pure ASGI middleware; requests whose `authorization` header is not `Bearer <expected_token>` get `401` with JSON body `{"detail": "unauthorized"}` without touching `app`; comparison via `hmac.compare_digest`.
  - `__main__.main() -> None` — argparse (`--config`, default env `GARM_MCP_CONFIG`) → `load_config` → `build_server` → `app = require_bearer(config.endpoint_token, mcp.streamable_http_app())` → `uvicorn.run(app, host, port)` parsed from `config.bind_address`. `--help` must exit 0 before any config is loaded.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth.py
from starlette.testclient import TestClient
from starlette.responses import PlainTextResponse
from starlette.applications import Starlette
from garm_mcp.auth import require_bearer

def make_app():
    async def endpoint(request):
        return PlainTextResponse("ok")
    inner = Starlette(routes=[("/tool", endpoint, ["GET"])])
    return require_bearer("s3cret", inner)

def test_missing_token_401():
    c = TestClient(make_app())
    assert c.get("/tool").status_code == 401

def test_wrong_token_401():
    c = TestClient(make_app())
    assert c.get("/tool", headers={"Authorization": "Bearer wrong"}).status_code == 401

def test_correct_token_passes_through():
    c = TestClient(make_app())
    assert c.get("/tool", headers={"Authorization": "Bearer s3cret"}).status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -v`
Expected: FAIL

- [ ] **Step 3: Implement middleware and entrypoint**

Middleware reads `scope["headers"]` (bytes, lowercase keys) for `authorization`; on match, forwards to the wrapped app. Entrypoint parses args before touching config so `--help` needs nothing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/garm_mcp/auth.py src/garm_mcp/__main__.py tests/test_auth.py
git commit -m "feat: bearer-token auth middleware and streamable HTTP entrypoint"
```

---

### Task 7: Example config, smoke script, README

**Files:**
- Create: `config.example.json`, `scripts/smoke_list_instances.py`, `README.md`
- Test: full suite run

**Interfaces:**
- Consumes: `load_config`, `summarize_statuses`, `GarmClient` (Tasks 1, 2, 4).
- Produces: operator-facing artifacts; `README.md` sections pinned below.

- [ ] **Step 1: Create `config.example.json`**

Exactly the JSON shape from Task 1 with `CHANGE_ME` placeholders for `endpoint_token` and both tokens.

- [ ] **Step 2: Create `scripts/smoke_list_instances.py`**

Loads config via `load_config()` (env `GARM_MCP_CONFIG`), builds `GarmClient` for the default deployment, calls `list_instances()`, prints `summarize_statuses(...)` as JSON, exits 1 on `GarmApiError`/`ConfigError` with the message on stderr. No MCP import needed — this validates connectivity and auth the way `restricted-jira-mcp`'s smoke script does.

- [ ] **Step 3: Create `README.md` with these pinned sections**

1. **What it is** — read-only GARM MCP server for AI agents; the threat-model summary from the spec (VM holds only the endpoint token; GARM JWTs stay on the host; root in the VM grants reads only).
2. **Host setup** — `uv sync`; `chmod 600` the config file (based on `config.example.json`); generate the endpoint token (`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`); run `uv run python -m garm_mcp --config /etc/garm-mcp/config.json`; bind address guidance (bind to an interface only the VM can reach).
3. **Agent/VM setup** — remote-MCP harness config snippet (URL + `Authorization: Bearer <endpoint token>` header) and the `npx mcp-remote <url> --header "Authorization: Bearer <endpoint token>"` fallback for stdio-only harnesses (v1 ships no custom bridge — documented fallback only).
4. **GARM authentication** — recommended: `username` + `password` per deployment in the host config; the server mints its own JWT and re-logins transparently when it expires (works unchanged on charm-managed GARM, which does not expose `jwt_auth.time_to_live`). Alternative: static `token` — raise `jwt_auth.time_to_live` in the GARM controller config (`[jwt_auth]` section, e.g. `time_to_live = "8760h"`, controller-wide, then restart), easiest source is copying `base_url` + `bearer_token` per manager profile from `~/.local/share/garm-cli/config.toml`, or mint fresh via `POST /api/v1/auth/login`; never hand-sign with `jwt_secret`. What expiry failure looks like (the pointed startup error from Task 1).
5. **Tools** — the 8 tool names with one-line purposes and the `deployment` enum behavior.
6. **Smoke validation** — how to run `scripts/smoke_list_instances.py`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS (all tests from Tasks 1–6)

- [ ] **Step 5: Verify entrypoint help and smoke script parse cleanly**

Run: `uv run python -m garm_mcp --help && uv run python scripts/smoke_list_instances.py --help`
Expected: both print usage and exit 0 without needing a config file.

- [ ] **Step 6: Commit**

```bash
git add config.example.json scripts/ README.md
git commit -m "docs: operator README, example config, smoke script"
```

---

## Out of scope (v1, per spec)

- Custom stdio↔HTTP bridge (`npx mcp-remote` is the documented fallback).
- Host-side bind beyond simple `host:port` (TLS termination left to the operator's reverse proxy or network posture).
- Credential/endpoint/proxy/webhook/metrics-token tools; any write tool.