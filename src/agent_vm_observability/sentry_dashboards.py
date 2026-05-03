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
            "period": "1h",
            "widgets": [
                _big_number("Active Sessions", "count_unique(session_id)", "is_transaction:true session_id:*", layout=_layout(0, 0, 1, 2)),
                _big_number("LLM Call Count", "count()", "is_transaction:true agent_model:*", layout=_layout(1, 0, 1, 2)),
                _big_number("Total Tokens", "sum(measurements.total_tokens)", "is_transaction:true", layout=_layout(2, 0, 2, 2)),
                _big_number("Estimated Cost", "sum(measurements.cost_usd)", "is_transaction:true", layout=_layout(4, 0, 2, 2)),
                _line(
                    "Agent Runs",
                    ["count_unique(session_id)"],
                    "is_transaction:true session_id:*",
                    layout=_layout(0, 2, 2, 3),
                ),
                _line(
                    "LLM Calls",
                    ["count()"],
                    "is_transaction:true agent_model:*",
                    layout=_layout(2, 2, 2, 3),
                ),
                _line(
                    "Duration",
                    ["avg(transaction.duration)", "p95(transaction.duration)"],
                    "is_transaction:true",
                    layout=_layout(4, 2, 2, 3),
                ),
                _bar(
                    "LLM Calls by Model",
                    ["count()", "agent_model"],
                    "is_transaction:true agent_model:*",
                    layout=_layout(0, 5, 2, 4),
                ),
                _line(
                    "Tokens Used",
                    ["sum(measurements.total_tokens)", "agent_model"],
                    "is_transaction:true agent_model:*",
                    layout=_layout(2, 5, 2, 4),
                ),
                _bar(
                    "Tool Calls",
                    ["count()", "tool_name"],
                    "is_transaction:true tool_name:*",
                    layout=_layout(4, 5, 2, 4),
                ),
                _line(
                    "Estimated Cost",
                    ["sum(measurements.cost_usd)", "agent_model"],
                    "is_transaction:true",
                    layout=_layout(0, 9, 2, 4),
                ),
                _bar(
                    "Coding Harness Distribution",
                    ["count()", "agent"],
                    "is_transaction:true agent:*",
                    layout=_layout(2, 9, 2, 4),
                ),
                _table(
                    "Usage by Project",
                    ["count()", "sum(measurements.total_tokens)", "sum(measurements.cost_usd)", "agent_project"],
                    "is_transaction:true agent_project:*",
                    layout=_layout(4, 9, 2, 4),
                ),
                _line(
                    "Failures",
                    ["count()"],
                    "is_transaction:true (success:false OR level:error)",
                    layout=_layout(0, 13, 2, 3),
                ),
                _table(
                    "High-cost Traces",
                    ["measurements.cost_usd", "measurements.total_tokens", "transaction.duration", "transaction", "agent", "agent_model", "timestamp"],
                    "is_transaction:true",
                    layout=_layout(2, 13, 2, 4),
                ),
                _table(
                    "Recent Failures",
                    ["timestamp", "transaction", "agent", "agent_project", "agent_model", "level"],
                    "agent:codex OR agent:claude-code OR agent:pi",
                    dataset="error-events",
                    layout=_layout(4, 13, 2, 4),
                ),
            ],
        },
    ]


def _layout(x: int, y: int, w: int, h: int) -> dict[str, int]:
    return {"x": x, "y": y, "w": w, "h": h, "minH": min(h, 2)}


def _table(title: str, fields: list[str], query: str, dataset: str = "spans", layout: dict[str, int] | None = None) -> dict[str, Any]:
    return _widget(title, "table", fields, query, dataset=dataset, layout=layout)


def _line(title: str, fields: list[str], query: str, dataset: str = "spans", layout: dict[str, int] | None = None) -> dict[str, Any]:
    return _widget(title, "line", fields, query, dataset=dataset, layout=layout)


def _bar(title: str, fields: list[str], query: str, dataset: str = "spans", layout: dict[str, int] | None = None) -> dict[str, Any]:
    return _widget(title, "bar", fields, query, dataset=dataset, layout=layout)


def _widget(title: str, display_type: str, fields: list[str], query: str, dataset: str = "spans", layout: dict[str, int] | None = None) -> dict[str, Any]:
    query_fields = _query_fields(fields)
    widget: dict[str, Any] = {
        "title": title,
        "displayType": display_type,
        "interval": "1m",
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
    if layout:
        widget["layout"] = layout
    if display_type in {"bar", "line", "area"} and query_fields["columns"]:
        widget["limit"] = 10
    return widget


def _big_number(title: str, field: str, query: str, dataset: str = "spans", layout: dict[str, int] | None = None) -> dict[str, Any]:
    widget = {
        "title": title,
        "displayType": "big_number",
        "interval": "1m",
        "queries": [{"name": title, "fields": [field], "query": query, "aggregates": [field], "columns": []}],
        "widgetType": _widget_type(dataset),
    }
    if layout:
        widget["layout"] = layout
    return widget


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
        if not dry_run and not self.config.sentry_org:
            raise RuntimeError("SENTRY_ORG is required to apply dashboards")
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
        payload: dict[str, Any] = {"title": spec["title"], "widgets": widgets, "period": spec.get("period", "24h")}
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
