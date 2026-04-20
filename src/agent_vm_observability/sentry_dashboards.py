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
                _big_number("Estimated cost (24h)", "sum(measurements.cost_usd)", "is_transaction:true"),
                _big_number("Total tokens (24h)", "sum(measurements.total_tokens)", "is_transaction:true"),
                _big_number("Trace volume (24h)", "count()", "is_transaction:true"),
                _table(
                    "Top models by cost",
                    ["sum(measurements.cost_usd)", "sum(measurements.total_tokens)", "count()", "agent_model"],
                    "is_transaction:true agent_model:*",
                ),
                _table(
                    "Top projects by cost",
                    ["sum(measurements.cost_usd)", "sum(measurements.total_tokens)", "count()", "agent_project"],
                    "is_transaction:true agent_project:*",
                ),
                _table(
                    "Top agents by cost",
                    ["sum(measurements.cost_usd)", "sum(measurements.total_tokens)", "count()", "agent"],
                    "is_transaction:true agent:*",
                ),
            ],
        },
        {
            "title": "Agent VM Token And Tool Health",
            "widgets": [
                _table(
                    "Largest token traces",
                    ["measurements.total_tokens", "measurements.cost_usd", "transaction.duration", "transaction", "agent_model", "timestamp"],
                    "is_transaction:true",
                ),
                _table(
                    "Slow traces",
                    ["transaction.duration", "measurements.total_tokens", "transaction", "agent", "timestamp"],
                    "is_transaction:true",
                ),
                _table(
                    "Tool activity",
                    ["count()", "sum(measurements.total_tokens)", "sum(measurements.cost_usd)", "tool_name"],
                    "is_transaction:true tool_name:*",
                ),
                _table(
                    "Claude usage",
                    ["count()", "sum(measurements.total_tokens)", "sum(measurements.cost_usd)", "transaction"],
                    "is_transaction:true agent:claude-code",
                ),
                _table(
                    "Codex usage",
                    ["count()", "sum(measurements.total_tokens)", "sum(measurements.cost_usd)", "transaction"],
                    "is_transaction:true agent:codex",
                ),
            ],
        },
        {
            "title": "Agent VM Failures And Sessions",
            "widgets": [
                _table(
                    "Recent sessions",
                    ["timestamp", "session_id", "agent", "agent_project", "agent_model", "measurements.cost_usd"],
                    "is_transaction:true session_id:*",
                ),
                _table(
                    "Failure traces",
                    ["timestamp", "transaction", "agent", "agent_project", "agent_model"],
                    "is_transaction:true (success:false OR level:error)",
                ),
                _table(
                    "High-cost traces",
                    ["measurements.cost_usd", "measurements.total_tokens", "transaction", "agent", "timestamp"],
                    "is_transaction:true",
                ),
                _table(
                    "Error-level events",
                    ["title", "count()", "level"],
                    "agent:codex OR agent:claude-code",
                    dataset="error-events",
                ),
            ],
        },
    ]


def _table(title: str, fields: list[str], query: str, dataset: str = "spans") -> dict[str, Any]:
    query_fields = _query_fields(fields)
    return {
        "title": title,
        "displayType": "table",
        "interval": "5m",
        "queries": [
            {
                "name": title,
                "fields": query_fields["fields"],
                "query": query,
                "orderby": _default_orderby(query_fields),
                "aggregates": query_fields["aggregates"],
                "columns": query_fields["columns"],
            }
        ],
        "widgetType": _widget_type(dataset),
    }


def _big_number(title: str, field: str, query: str, dataset: str = "spans") -> dict[str, Any]:
    return {
        "title": title,
        "displayType": "big_number",
        "interval": "5m",
        "queries": [{"name": title, "fields": [field], "query": query, "aggregates": [field], "columns": []}],
        "widgetType": _widget_type(dataset),
    }


def _query_fields(fields: list[str]) -> dict[str, list[str]]:
    aggregates = [field for field in fields if "(" in field and field.endswith(")")]
    columns = [field for field in fields if field not in aggregates]
    return {"fields": [*aggregates, *columns], "aggregates": aggregates, "columns": columns}


def _default_orderby(query_fields: dict[str, list[str]]) -> str:
    if query_fields["aggregates"]:
        return f"-{query_fields['aggregates'][0]}"
    if "transaction.duration" in query_fields["columns"]:
        return "-transaction.duration"
    if "measurements.cost_usd" in query_fields["columns"]:
        return "-measurements.cost_usd"
    if "measurements.total_tokens" in query_fields["columns"]:
        return "-measurements.total_tokens"
    return query_fields["columns"][0] if query_fields["columns"] else "-timestamp"


def _widget_type(dataset: str) -> str:
    return "error-events" if dataset == "error-events" else "spans"


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
        if not dry_run and not self.token:
            raise RuntimeError("SENTRY_AUTH_TOKEN is required to apply dashboards")
        existing = self._list_dashboards() if not dry_run else []
        results: list[DashboardApplyResult] = []
        for spec in dashboard_specs():
            payload = self._payload(spec)
            if dry_run:
                results.append(DashboardApplyResult(spec["title"], "dry-run", payload))
                continue
            dashboard = next((item for item in existing if item.get("title") == spec["title"]), None)
            if dashboard:
                results.append(self._put_dashboard(int(dashboard["id"]), spec["title"], payload))
            else:
                results.append(self._post_dashboard(spec["title"], payload))
        return results

    def _payload(self, spec: dict[str, Any]) -> dict[str, Any]:
        widgets = []
        for widget in spec["widgets"]:
            clone = json.loads(json.dumps(widget))
            for query in clone.get("queries", []):
                query["conditions"] = query.pop("query", "")
                query["fields"] = query.get("fields", [])
                query["aggregates"] = query.get("aggregates", [])
                query["columns"] = query.get("columns", [])
                query["fieldAliases"] = [""] * len(query["fields"])
            widgets.append(clone)
        payload: dict[str, Any] = {"title": spec["title"], "widgets": widgets, "period": "24h"}
        if self.config.sentry_project_id:
            payload["projects"] = [int(self.config.sentry_project_id)]
        return payload

    def _list_dashboards(self) -> list[dict[str, Any]]:
        raw = self._request_json(
            f"{self.base_url}/api/0/organizations/{self.config.sentry_org}/dashboards/",
            method="GET",
        )
        return raw if isinstance(raw, list) else []

    def _post_dashboard(self, title: str, payload: dict[str, Any]) -> DashboardApplyResult:
        try:
            response = self._request_json(
                f"{self.base_url}/api/0/organizations/{self.config.sentry_org}/dashboards/",
                method="POST",
                payload=payload,
            )
            return DashboardApplyResult(title, "created", response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return DashboardApplyResult(title, f"failed:{exc.code}", body)

    def _put_dashboard(self, dashboard_id: int, title: str, payload: dict[str, Any]) -> DashboardApplyResult:
        try:
            response = self._request_json(
                f"{self.base_url}/api/0/organizations/{self.config.sentry_org}/dashboards/{dashboard_id}/",
                method="PUT",
                payload=payload,
            )
            return DashboardApplyResult(title, "updated", response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return DashboardApplyResult(title, f"failed:{exc.code}", body)

    def _request_json(self, url: str, *, method: str, payload: dict[str, Any] | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else None
