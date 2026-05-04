from datetime import datetime, timezone

from agent_vm_observability.cli import export_sentry_usage_rollups, main
from agent_vm_observability.config import RuntimeConfig
from agent_vm_observability.memory import MemoryStore
from agent_vm_observability.model import NormalizedTrace


def make_config(tmp_path) -> RuntimeConfig:
    return RuntimeConfig(
        config_path=tmp_path / "config.env",
        legacy_config_path=tmp_path / "legacy.env",
        state_path=tmp_path / "state.json",
        memory_db_path=tmp_path / "memory.db",
        codex_logs_db=tmp_path / "logs.sqlite",
        codex_state_db=tmp_path / "state.sqlite",
        claude_projects_glob="",
        claude_mem_db=tmp_path / "claude-mem.db",
        pi_suggester_glob="",
        sentry_dsn=None,
        sentry_org="example-org",
        sentry_project="agent-vm-usage",
        sentry_project_id="123",
        include_text=False,
        traces_sample_rate=1.0,
        max_batch=250,
        poll_seconds=15,
        record_memory=True,
    )


def test_self_test_dry_run_smoke(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_VM_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_VM_STATE", str(tmp_path / "state.json"))
    assert main(["self-test", "--dry-run"]) == 0


def test_status_smoke(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_VM_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_VM_STATE", str(tmp_path / "state.json"))
    assert main(["status"]) == 0


def test_backfill_succeeds_without_sentry_when_memory_is_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SENTRY_DSN", "")
    monkeypatch.setenv("AGENT_SENTRY_DSN", "")
    monkeypatch.setenv("AGENT_VM_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_VM_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("AGENT_VM_CODEX_LOGS_DB", str(tmp_path / "missing-logs.sqlite"))
    monkeypatch.setenv("AGENT_VM_CODEX_STATE_DB", str(tmp_path / "missing-state.sqlite"))
    monkeypatch.setenv("AGENT_VM_CLAUDE_GLOB", str(tmp_path / "missing-claude/**/*.jsonl"))
    monkeypatch.setenv("AGENT_VM_PI_SUGGESTER_GLOB", str(tmp_path / "missing-pi"))

    assert main(["backfill", "--minutes", "5"]) == 0


def test_export_sentry_usage_rollups_emits_event_and_aggregate_rows(tmp_path, capsys) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.record_trace(
        NormalizedTrace(
            agent="pi",
            kind="suggestion.generated",
            timestamp=datetime.now(timezone.utc),
            source_event_id="pi-usage-1",
            session_id="session-1",
            model="gpt-5.5",
            project="example",
            measurements={"total_tokens": 42, "cost_usd": 0.01},
        )
    )

    assert export_sentry_usage_rollups(make_config(tmp_path), store, minutes=60, dry_run=True) == 0

    output = capsys.readouterr().out
    assert "usage_rollup': 'event'" in output
    assert "usage_rollup': 'total'" in output
    assert "usage_rollup': 'agent'" in output
    assert "usage_rollup': 'model'" in output
    assert "usage_rollup': 'project'" in output


def test_memory_context_accepts_pi_agent(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("AGENT_VM_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_VM_STATE", str(tmp_path / "state.json"))

    assert main(["memory", "context", "--cwd", str(tmp_path), "--agent", "pi"]) == 0

    assert "target_agent: pi" in capsys.readouterr().out
