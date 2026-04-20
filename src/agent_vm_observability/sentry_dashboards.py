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
                _big_number("Events by agent", "count()", ["is_transaction:true agent:codex", "is_transaction:true agent:claude-code"], "transaction-like"),
                _table("Recent traces", ["transaction", "count()"], "is_transaction:true", "transaction-like"),
                _table("Claude traces", ["transaction", "count()"], "is_transaction:true transaction:claude*", "transaction-like"),
                _table("Codex traces", ["transaction", "count()"], "is_transaction:true transaction:codex*", "transaction-like"),
                _table("Tool traces", ["transaction", "count()"], "is_transaction:true tool_name:* OR transaction:*tool*", "transaction-like"),
                _table("Error-level events", ["title", "count()"], "level:error agent:codex OR agent:claude-code", "error-events"),
            ],
        },
        {
            "title": "Agent VM Tool And Token Health",
            "widgets": [
                _table("Slow traces", ["transaction.duration", "transaction", "timestamp"], "is_transaction:true", "transaction-like"),
                _table("Claude assistant turns", ["transaction", "count()"], "is_transaction:true transaction:claude.assistant_turn", "transaction-like"),
                _table("Claude tools", ["transaction", "count()"], "is_transaction:true transaction:claude.tool_*", "transaction-like"),
                _table("Codex websocket activity", ["transaction", "count()"], "is_transaction:true transaction:codex.codex.websocket*", "transaction-like"),
                _table("Error-level agent events", ["title", "level", "count()"], "level:error agent:codex OR agent:claude-code", "error-events"),
            ],
        },
    ]


def _table(title: str, fields: list[str], query: str, dataset: str) -> dict[str, Any]:
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


def _big_number(title: str, field: str, queries: list[str], dataset: str) -> dict[str, Any]:
    return {
        "title": title,
        "displayType": "big_number",
        "interval": "5m",
        "queries": [{"name": q or title, "fields": [field], "query": q, "aggregates": [field], "columns": []} for q in queries],
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
    return query_fields["columns"][0] if query_fields["columns"] else "-timestamp"


def _widget_type(dataset: str) -> str:
    if dataset == "spans":
        return "spans"
    if dataset in {"transaction-like", "error-events"}:
        return "spans" if dataset == "transaction-like" else "error-events"
    if dataset == "transactions":
        return "spans"
    return "error-events"


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
            for query in widget.get("queries", []):
                query["conditions"] = query.pop("query", "")
                query["fields"] = query.get("fields", [])
                query["aggregates"] = query.get("aggregates", [])
                query["columns"] = query.get("columns", [])
                query["fieldAliases"] = [""] * len(query["fields"])
            widgets.append(widget)
        payload: dict[str, Any] = {"title": spec["title"], "widgets": widgets}
        if project_id:
            payload["projects"] = [int(project_id)]
        return payload

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
