"""Small HTTP boundary for the Cloudflare Worker API.

No command writes D1 directly. Keeping this adapter independent lets tests use a
fake client and lets the Worker implementation evolve without coupling it to the
catalog domain.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Mapping, Optional, Sequence


class StorageClientError(RuntimeError):
    pass


class WorkerAPIClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("INTELLIGENCE_API_URL", "")).rstrip("/")
        self.token = token or os.getenv("INTELLIGENCE_API_TOKEN", "")
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/health", authenticated=False)

    def sync_catalog(
        self, catalog: Mapping[str, Any], idempotency_key: str
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/v1/catalog/sync",
            body={"catalog": dict(catalog)},
            headers={"Idempotency-Key": idempotency_key},
        )

    def get_catalog(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/catalog")

    def get_due_channels(
        self,
        *,
        limit: int = 100,
        now: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "GET",
            self._path(
                "/v1/channels/due",
                {"limit": limit, "now": now, "target_id": target_id},
            ),
        )

    def write_items(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        idempotency_key: str,
        channel_state: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"items": [dict(item) for item in items]}
        if channel_state is not None:
            body["channel_state"] = dict(channel_state)
        return self._request(
            "POST",
            "/v1/items/batch",
            body=body,
            headers={"Idempotency-Key": idempotency_key},
        )

    def get_pending_analysis(
        self,
        *,
        limit: int = 100,
        target_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        since: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "GET",
            self._path(
                "/v1/items/pending-analysis",
                {"limit": limit, "target_id": target_id, "channel_id": channel_id, "since": since},
            ),
        )

    def write_analyses(
        self,
        analyses: Sequence[Mapping[str, Any]],
        *,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/v1/analyses/batch",
            body={"analyses": [dict(item) for item in analyses]},
            headers={"Idempotency-Key": idempotency_key},
        )

    def get_report_input(
        self,
        *,
        window_start: str,
        window_end: str,
        min_importance: int = 1,
        limit: int = 500,
        target_id: Optional[str] = None,
        tag: Optional[str] = None,
        include_reported: bool = False,
    ) -> Dict[str, Any]:
        return self._request(
            "GET",
            self._path(
                "/v1/reports/input",
                {
                    "from": window_start,
                    "to": window_end,
                    "min_importance": min_importance,
                    "limit": limit,
                    "target_id": target_id,
                    "tag": tag,
                    "include_reported": "true" if include_reported else "false",
                },
            ),
        )

    def get_report(self, report_id: str) -> Dict[str, Any]:
        return self._request("GET", "/v1/reports/%s" % urllib.parse.quote(report_id, safe=""))

    def create_report(
        self, report: Mapping[str, Any], *, idempotency_key: str
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/v1/reports",
            body=report,
            headers={"Idempotency-Key": idempotency_key},
        )

    def update_report_status(
        self,
        report_id: str,
        report_status: str,
        *,
        idempotency_key: str,
        published_url: Optional[str] = None,
        git_commit: Optional[str] = None,
        published_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        body = {
            key: value
            for key, value in {
                "report_status": report_status,
                "published_url": published_url,
                "git_commit": git_commit,
                "published_at": published_at,
            }.items()
            if value is not None
        }
        return self._request(
            "PATCH",
            "/v1/reports/%s/status" % urllib.parse.quote(report_id, safe=""),
            body=body,
            headers={"Idempotency-Key": idempotency_key},
        )

    def revise_published_report(
        self,
        report_id: str,
        *,
        title: str,
        content_markdown: str,
        reason: str,
        git_commit: str,
        expected_git_commit: str,
        idempotency_key: str,
        item_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "title": title,
            "content_markdown": content_markdown,
            "reason": reason,
            "git_commit": git_commit,
            "expected_git_commit": expected_git_commit,
        }
        if item_ids is not None:
            body["item_ids"] = list(item_ids)
        return self._request(
            "PATCH",
            "/v1/reports/%s/editorial-revision"
            % urllib.parse.quote(report_id, safe=""),
            body=body,
            headers={"Idempotency-Key": idempotency_key},
        )

    def create_run(
        self, run: Mapping[str, Any], *, idempotency_key: str
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/v1/runs",
            body=run,
            headers={"Idempotency-Key": idempotency_key},
        )

    def update_run(
        self,
        run_id: str,
        update: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        return self._request(
            "PATCH",
            "/v1/runs/%s" % urllib.parse.quote(run_id, safe=""),
            body=update,
            headers={"Idempotency-Key": idempotency_key},
        )

    def create_audit_event(
        self, event: Mapping[str, Any], *, idempotency_key: str
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/v1/audit-events",
            body=event,
            headers={"Idempotency-Key": idempotency_key},
        )

    def list_audit_events(
        self,
        *,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        return self._request(
            "GET",
            self._path(
                "/v1/audit-events",
                {"entity_type": entity_type, "entity_id": entity_id, "limit": limit},
            ),
        )

    def get_run(self, run_id: str) -> Dict[str, Any]:
        safe_id = urllib.parse.quote(run_id, safe="")
        return self._request("GET", "/v1/runs/%s" % safe_id)

    def list_runs(
        self, status: Optional[str] = None, limit: int = 20
    ) -> Dict[str, Any]:
        query = {"limit": str(limit)}
        if status:
            query["status"] = status
        return self._request("GET", "/v1/runs?%s" % urllib.parse.urlencode(query))

    @staticmethod
    def _path(path: str, query: Mapping[str, Any]) -> str:
        values = {key: str(value) for key, value in query.items() if value is not None}
        return "%s?%s" % (path, urllib.parse.urlencode(values)) if values else path

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        authenticated: bool = True,
    ) -> Dict[str, Any]:
        if not self.base_url:
            raise StorageClientError("INTELLIGENCE_API_URL is not configured")
        if authenticated and not self.token:
            raise StorageClientError("INTELLIGENCE_API_TOKEN is not configured")

        request_headers = {
            "Accept": "application/json",
            # Cloudflare may reject urllib's default Python-urllib signature
            # before the request reaches the Worker (Error 1010).  A stable,
            # honest application identifier also makes request logs useful.
            "User-Agent": "personal-intelligence/0.1 (+https://github.com/fatflowers/fatflowers.github.io)",
        }
        request_headers.update(dict(headers or {}))
        if authenticated:
            request_headers["Authorization"] = "Bearer %s" % self.token
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            "%s%s" % (self.base_url, path),
            data=data,
            headers=request_headers,
            method=method,
        )
        attempts = 3 if method == "GET" or request.get_header("Idempotency-key") else 1
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = response.read().decode("utf-8")
                    return json.loads(payload) if payload else {}
            except urllib.error.HTTPError as exc:
                if exc.code in {429, 502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                body_text = exc.read().decode("utf-8", errors="replace")[:1000]
                raise StorageClientError("Worker API returned HTTP %d: %s" % (exc.code, body_text)) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise StorageClientError("Worker API request failed: %s" % exc) from exc
            except json.JSONDecodeError as exc:
                raise StorageClientError("Worker API returned invalid JSON") from exc
