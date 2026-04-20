from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import RuntimeConfig


def dashboard_specs() -> list[dict[str, Any]]:
    return [
        {
            "title": "Agent VM Usage Overview",
            "widgets": [
                _big_number("Events by agent", "count()", ["agent:codex OR agent:claude-code"]),
                _table("Recent trace types", ["title", "event.type", "count()"], "", "discover"),
                _table("Transactions by name", ["transaction", "count()"], "event.type:transaction", "transactions"),
                _table("Claude traces", ["title", "event.type", "count()"], "title:claude*", "discover"),
                _table("Codex traces", ["title", "event.type", "count()"], "title:codex*", "discover"),
                _table("Tool traces", ["title", "count()"], "tool_name:* OR title:*tool*", "discover"),
            ],
        },
        {
            "title": "Agent VM Tool And Token Health",
            "widgets": [
                _table("Slow traces", ["title", "transaction.duration", "timestamp"], "event.type:transaction", "transactions"),
                _table("Claude assistant turns", ["title", "count()"], "title:claude.assistant_turn", "discover"),
                _table("Claude tools", ["title", "count()"], "title:claude.tool_*", "discover"),
                _table("Codex websocket activity", ["title", "count()"], "title:codex.codex.websocket*", "discover"),
                _table("Error-level agent events", ["title", "level", "count()"], "level:error", "discover"),
            ],
        },
    ]


def _table(title: str, fields: list[str], query: str, dataset: str) -> dict[str, Any]:
    return {
        "title": title,
        "displayType": "table",
        "interval": "5m",
        "queries": [{"name": title, "fields": fields, "query": query, "orderby": "-count()", "aggregates": fields, "columns": fields}],
        "widgetType": dataset,
    }


def _big_number(title: str, field: str, queries: list[str]) -> dict[str, Any]:
    return {
        "title": title,
        "displayType": "big_number",
        "interval": "5m",
        "queries": [{"name": q or title, "fields": [field], "query": q, "aggregates": [field], "columns": []} for q in queries],
        "widgetType": "discover",
    }


@dataclass
class DashboardApplyResult:
    title: str
    status: str
    response: dict[str, Any] | str | None = None


class SentryDashboardClient:
    def __init__(self, config: RuntimeConfig, token: str | None = None, base_url: str = "https://sentry.io") -> None:
        self.config = config
        self.token = token or os.environ.get("SENTRY_AUTH_TOKEN")
        self.base_url = base_url.rstrip("/")

    def apply(self, dry_run: bool = False) -> list[DashboardApplyResult]:
        results: list[DashboardApplyResult] = []
        for spec in dashboard_specs():
            payload = self._payload(spec)
            if dry_run:
                results.append(DashboardApplyResult(spec["title"], "dry-run", payload))
                continue
            if not self.token:
                raise RuntimeError("SENTRY_AUTH_TOKEN is required to apply dashboards")
            results.append(self._post_dashboard(spec["title"], payload))
        return results

    def _payload(self, spec: dict[str, Any]) -> dict[str, Any]:
        project_id = self.config.sentry_project_id
        widgets = []
        for widget in spec["widgets"]:
            widget = json.loads(json.dumps(widget))
            if project_id:
                for query in widget.get("queries", []):
                    query["conditions"] = query.pop("query", "")
                    query["fields"] = query.get("fields", [])
                    query["aggregates"] = query.get("aggregates", [])
                    query["columns"] = query.get("columns", [])
                    query["fieldAliases"] = []
                widget["projects"] = [int(project_id)]
            widgets.append(widget)
        return {"title": spec["title"], "widgets": widgets}

    def _post_dashboard(self, title: str, payload: dict[str, Any]) -> DashboardApplyResult:
        url = f"{self.base_url}/api/0/organizations/{self.config.sentry_org}/dashboards/"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return DashboardApplyResult(title, f"created:{response.status}", json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 409 or "already exists" in body.lower():
                return DashboardApplyResult(title, "already-exists", body)
            return DashboardApplyResult(title, f"failed:{exc.code}", body)

