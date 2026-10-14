import time
from urllib.parse import quote

import requests

from garm_mcp.errors import GarmApiError

RETRY_STATUSES = {502, 503, 504}


class GarmClient:
    def __init__(
        self,
        name: str,
        base_url: str,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        ca_bundle: str | None = None,
        timeout: float = 10.0,
        retry_delay: float = 0.5,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.username = username
        self.password = password
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.session = requests.Session()
        if ca_bundle:
            self.session.verify = ca_bundle

    def _has_credentials(self) -> bool:
        return bool(self.username and self.password)

    def login(self) -> str:
        resp = self._send(
            "POST",
            f"{self.base_url}/api/v1/auth/login",
            json={"username": self.username, "password": self.password},
        )
        self.token = self._parse(resp)["token"]
        return self.token

    def _send(self, method: str, url: str, **kwargs) -> requests.Response:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        for attempt in range(2):
            try:
                resp = self.session.request(
                    method, url, headers=headers, timeout=self.timeout, **kwargs
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == 0:
                    time.sleep(self.retry_delay)
                    continue
                raise GarmApiError(f"GARM request failed: {exc}") from exc
            if resp.status_code in RETRY_STATUSES and attempt == 0:
                time.sleep(self.retry_delay)
                continue
            return resp
        raise AssertionError("unreachable")

    def _parse(self, resp: requests.Response):
        if resp.ok:
            return resp.json()
        raise GarmApiError(message=self._error_message(resp), status_code=resp.status_code)

    def _error_message(self, resp: requests.Response) -> str:
        try:
            error = resp.json()["error"]
        except (ValueError, KeyError, TypeError):
            return f"unknown error (HTTP {resp.status_code})"
        if isinstance(error, dict):
            message = error.get("message")
            if not isinstance(message, str) or not message:
                return f"unknown error (HTTP {resp.status_code})"
            details = error.get("details") if isinstance(error.get("details"), str) else None
        elif isinstance(error, str):
            message = error
            details = resp.json().get("details") if isinstance(resp.json().get("details"), str) else None
        else:
            return f"unknown error (HTTP {resp.status_code})"
        if details:
            return f"{message}: {details}"
        return message

    def _get(self, path: str):
        if not self.token and self._has_credentials():
            self.login()
        resp = self._send("GET", f"{self.base_url}/api/v1/{path}")
        if resp.status_code == 401 and self._has_credentials():
            self.login()
            resp = self._send("GET", f"{self.base_url}/api/v1/{path}")
        return self._parse(resp)

    def list_instances(self) -> list:
        return self._get("instances")

    def get_instance(self, instance_name: str) -> dict:
        return self._get(f"instances/{quote(instance_name, safe='')}")

    def list_scalesets(self) -> list:
        return self._get("scalesets")

    def list_scaleset_instances(self, scaleset_id: int) -> list:
        return self._get(f"scalesets/{scaleset_id}/instances")

    def list_jobs(self) -> list:
        results, _ = self.list_jobs_page()
        return results

    def list_jobs_page(self) -> tuple:
        """Fetch jobs, handling both plain-list and paginated response shapes."""
        payload = self._get("jobs?page=1&pageSize=250")
        if isinstance(payload, list):
            return payload, None
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            total = payload.get("total_count")
            return payload["results"], (total if isinstance(total, int) else None)
        raise GarmApiError(
            message="unexpected response shape for /jobs",
            status_code=None,
        )

    def list_repositories(self) -> list:
        return self._get("repositories")

    def list_organizations(self) -> list:
        return self._get("organizations")

    def list_enterprises(self) -> list:
        return self._get("enterprises")

    def get_controller_info(self) -> dict:
        return self._get("controller-info")
