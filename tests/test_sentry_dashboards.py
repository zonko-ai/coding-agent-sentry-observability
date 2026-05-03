from pathlib import Path

from agent_vm_observability.config import RuntimeConfig
from agent_vm_observability.sentry_dashboards import SentryDashboardClient, dashboard_specs


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
    assert dashboard["period"] == "1h"

    widgets = dashboard["widgets"]
    assert len(widgets) >= 12
    assert {widget["displayType"] for widget in widgets} >= {"big_number", "line", "bar", "table"}

    widget_titles = {widget["title"] for widget in widgets}
    assert {
        "Agent Runs",
        "LLM Calls",
        "Duration",
        "LLM Calls by Model",
        "Tokens Used",
        "Tool Calls",
        "Coding Harness Distribution",
        "Estimated Cost",
        "Failures",
    }.issubset(widget_titles)


def test_dashboard_payload_preserves_chart_layout_and_uses_one_hour_period() -> None:
    client = SentryDashboardClient(make_config(), token="token")

    payload = client._payload(dashboard_specs()[0])

    assert payload["period"] == "1h"
    assert payload["projects"] == [123]
    assert len(payload["widgets"]) >= 12
    duration = next(widget for widget in payload["widgets"] if widget["title"] == "Duration")
    assert duration["displayType"] == "line"
    assert duration["layout"] == {"x": 4, "y": 2, "w": 2, "h": 3, "minH": 2}
    assert duration["queries"][0]["conditions"] == "is_transaction:true"
    assert duration["queries"][0]["aggregates"] == ["avg(transaction.duration)", "p95(transaction.duration)"]
    by_model = next(widget for widget in payload["widgets"] if widget["title"] == "LLM Calls by Model")
    assert by_model["displayType"] == "bar"
    assert by_model["limit"] == 10
    harnesses = next(widget for widget in payload["widgets"] if widget["title"] == "Coding Harness Distribution")
    assert harnesses["displayType"] == "bar"
    assert harnesses["limit"] == 10
    assert harnesses["queries"][0]["conditions"] == "is_transaction:true agent:*"
    assert harnesses["queries"][0]["fields"] == ["count()", "agent"]
