import json
from pathlib import Path

from agent_vm_observability.config import RuntimeConfig
from agent_vm_observability.ingest import AgentIngestor
from agent_vm_observability.sentry_sink import SentrySink
from agent_vm_observability.state import empty_state


def make_config(tmp_path: Path, pi_root: Path) -> RuntimeConfig:
    return RuntimeConfig(
        config_path=tmp_path / "env",
        legacy_config_path=tmp_path / "legacy-env",
        state_path=tmp_path / "state.json",
        memory_db_path=tmp_path / "memory.db",
        codex_logs_db=tmp_path / "missing-codex-logs.sqlite",
        codex_state_db=tmp_path / "missing-codex-state.sqlite",
        claude_projects_glob=str(tmp_path / "missing-claude" / "*.jsonl"),
        claude_mem_db=tmp_path / "missing-claude-mem.db",
        pi_suggester_glob=str(pi_root),
        sentry_dsn=None,
        sentry_org="example-org",
        sentry_project="agent-vm-usage",
        sentry_project_id=None,
        include_text=False,
        traces_sample_rate=1.0,
        max_batch=10,
        poll_seconds=15,
        record_memory=True,
    )


def test_process_once_exports_pi_suggester_events(tmp_path: Path) -> None:
    pi_root = tmp_path / ".pi" / "suggester"
    log_path = pi_root / "logs" / "events.ndjson"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        json.dumps(
            {
                "at": "2026-05-03T06:12:05.162Z",
                "level": "info",
                "message": "suggestion.generated",
                "meta": {
                    "turnId": "a11350ae",
                    "strategy": "compact",
                    "inputTokens": 4635,
                    "outputTokens": 128,
                    "cacheReadTokens": 0,
                    "cacheWriteTokens": 0,
                    "totalTokens": 4763,
                    "cost": 0.027015,
                    "preview": "can you make it use oauth instead?",
                },
            }
        )
        + "\n"
    )

    config = make_config(tmp_path, pi_root)
    sink = SentrySink(config, dry_run=True)
    state = empty_state()
    counts = AgentIngestor(config, sink).process_once(state, max_batch=10)

    assert counts["pi_records"] == 1
    assert state["pi_files"][str(log_path)]["offset"] == log_path.stat().st_size
    assert [trace.title for trace in sink.captured] == ["pi.suggestion.generated"]
    assert sink.captured[0].measurements["cost_usd"] == 0.027015
