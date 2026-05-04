from pathlib import Path

from agent_vm_observability.config import RuntimeConfig
from agent_vm_observability.sentry_dashboards import SentryDashboardClient, dashboard_specs
from agent_vm_observability.sentry_sink import USAGE_SCHEMA


def make_config() -> RuntimeConfig:
    return RuntimeConfig(
        config_path=Path("config.env"),
        legacy_config_path=Path("legacy.env"),
        state_path=Path("state.json"),
        memory_db_path=Path("memory.db"),
        codex_logs_db=Path("logs.sqlite"),
        codex_state_db=Path("state.sqlite"),
        claude_projects_glob="",
        claude_mem_db=Path("claude-mem.db"),
        pi_suggester_glob="",
        sentry_dsn="https://example.invalid/1",
        sentry_org="example-org",
        sentry_project="agent-vm-usage",
        sentry_project_id="123",
        include_text=False,
        traces_sample_rate=1.0,
        max_batch=250,
        poll_seconds=15,
        record_memory=True,
    )


def test_dashboard_specs_define_one_chart_heavy_usage_dashboard() -> None:
    specs = dashboard_specs()

    assert len(specs) == 1
    dashboard = specs[0]
    assert dashboard["title"] == "Agent VM Usage Overview"
    assert dashboard["period"] == "7d"

    widgets = dashboard["widgets"]
    assert len(widgets) >= 12
    assert {widget["displayType"] for widget in widgets} >= {"big_number", "line", "bar", "table"}

    widget_titles = {widget["title"] for widget in widgets}
    assert {
        "Agent Runs",
        "LLM Calls",
        "Span Duration",
        "LLM Calls by Model",
        "Tokens by Model",
        "Tool Calls",
        "LLM Calls by Agent",
        "Estimated Cost",
        "Cost by Agent",
        "Failures",
    }.issubset(widget_titles)


def test_dashboard_payload_preserves_chart_layout_and_uses_seven_day_period() -> None:
    client = SentryDashboardClient(make_config(), token="token")

    payload = client._payload(dashboard_specs()[0])

    assert payload["period"] == "7d"
    assert payload["projects"] == [123]
    assert len(payload["widgets"]) >= 12
    cost = next(widget for widget in payload["widgets"] if widget["title"] == "Estimated Cost")
    assert cost["widgetType"] == "spans"
    event_query = f"is_transaction:true usage_schema:{USAGE_SCHEMA} usage_canonical:true usage_rollup:event span.op:gen_ai.invoke_agent"
    assert cost["queries"][0]["conditions"] == f"{event_query} gen_ai.cost.total_tokens:>0"
    assert cost["queries"][0]["aggregates"] == ["sum(gen_ai.cost.total_tokens)"]
    duration = next(widget for widget in payload["widgets"] if widget["title"] == "Span Duration")
    assert duration["displayType"] == "line"
    assert duration["layout"] == {"x": 0, "y": 5, "w": 3, "h": 3, "minH": 2}
    assert duration["queries"][0]["conditions"] == event_query
    assert duration["queries"][0]["aggregates"] == ["avg(span.duration)", "p95(span.duration)"]
    by_model = next(widget for widget in payload["widgets"] if widget["title"] == "LLM Calls by Model")
    assert by_model["displayType"] == "bar"
    assert by_model["limit"] == 10
    assert by_model["queries"][0]["conditions"] == f"{event_query} usage_model:*"
    assert by_model["queries"][0]["fields"] == ["count()", "usage_model"]
    by_agent = next(widget for widget in payload["widgets"] if widget["title"] == "LLM Calls by Agent")
    assert by_agent["displayType"] == "bar"
    assert by_agent["limit"] == 10
    assert by_agent["queries"][0]["conditions"] == f"{event_query} agent:*"
    assert by_agent["queries"][0]["fields"] == ["count()", "agent"]
    high_cost = next(widget for widget in payload["widgets"] if widget["title"] == "High-cost Traces")
    assert high_cost["layout"] == {"x": 0, "y": 20, "w": 6, "h": 4, "minH": 2}
    assert high_cost["widgetType"] == "spans"
    assert high_cost["queries"][0]["conditions"] == f"{event_query} gen_ai.cost.total_tokens:>0"
    assert high_cost["queries"][0]["orderby"] == "-gen_ai.cost.total_tokens"
