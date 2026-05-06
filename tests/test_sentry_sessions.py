from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_vm_observability.config import RuntimeConfig
from agent_vm_observability.memory import MemoryStore
from agent_vm_observability.model import NormalizedTrace
from agent_vm_observability.sentry_sessions import capture_sentry_session_rows
from agent_vm_observability.sentry_sink import SentrySink, USAGE_SCHEMA


class TraceSink:
    def __init__(self) -> None:
        self.traces: list[NormalizedTrace] = []

    def capture(self, trace: NormalizedTrace) -> None:
        self.traces.append(trace)


def make_config(tmp_path: Path) -> RuntimeConfig:
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


def test_capture_sentry_session_rows_exports_latest_session_heartbeat(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.db")
    config = make_config(tmp_path)
    sink = SentrySink(config, dry_run=True)
    state: dict[str, object] = {}
    started = datetime.now(timezone.utc) - timedelta(minutes=5)
    latest = started + timedelta(minutes=2)

    memory.record_trace(
        NormalizedTrace(
            agent="codex",
            kind="codex.sse_event",
            timestamp=started,
            source_event_id="event-1",
            session_id="session-1",
            project="example",
            cwd="/tmp/example",
            model="gpt-5.5",
            token_usage={"total_tokens": 10},
        )
    )
    memory.record_trace(
        NormalizedTrace(
            agent="codex",
            kind="codex.sse_event",
            timestamp=latest,
            source_event_id="event-2",
            session_id="session-1",
            project="example",
            cwd="/tmp/example",
            model="gpt-5.5",
            token_usage={"total_tokens": 20},
        )
    )

    assert capture_sentry_session_rows(sink, memory, minutes=60, state=state) == 1
    assert capture_sentry_session_rows(sink, memory, minutes=60, state=state) == 0

    captured = sink.captured[0]
    assert captured.title == "codex.session_v9"
    assert captured.tags["usage_schema"] == USAGE_SCHEMA
    assert captured.tags["usage_rollup"] == "session"
    assert captured.tags["agent"] == "codex"
    assert captured.tags["agent_project"] == "example"
    assert captured.tags["usage_model"] == "gpt-5.5"
    assert captured.measurements["session_events"] == 2


def test_capture_sentry_session_rows_clamps_old_timestamps_for_sentry(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.db")
    sink = TraceSink()
    old = datetime.now(timezone.utc) - timedelta(days=6)

    memory.record_trace(
        NormalizedTrace(
            agent="codex",
            kind="codex.sse_event",
            timestamp=old,
            source_event_id="event-1",
            session_id="session-1",
            project="example",
        )
    )

    assert capture_sentry_session_rows(sink, memory, minutes=10080, state={}) == 1  # type: ignore[arg-type]
    assert sink.traces[0].timestamp is not None
    assert sink.traces[0].timestamp > datetime.now(timezone.utc) - timedelta(days=5)
    assert sink.traces[0].tags["session_timestamp_clamped"] == "true"


def test_capture_sentry_session_rows_emits_again_when_session_advances(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.db")
    config = make_config(tmp_path)
    sink = SentrySink(config, dry_run=True)
    state: dict[str, object] = {}
    started = datetime.now(timezone.utc) - timedelta(minutes=5)

    memory.record_trace(
        NormalizedTrace(
            agent="pi",
            kind="suggestion.generated",
            timestamp=started,
            source_event_id="pi-event-1",
            session_id="session-1",
            project="example",
            model="gpt-5.5",
            measurements={"cost_usd": 0.01},
        )
    )
    assert capture_sentry_session_rows(sink, memory, minutes=60, state=state) == 1

    memory.record_trace(
        NormalizedTrace(
            agent="pi",
            kind="suggestion.generated",
            timestamp=started + timedelta(minutes=1),
            source_event_id="pi-event-2",
            session_id="session-1",
            project="example",
            model="gpt-5.5",
            measurements={"cost_usd": 0.02},
        )
    )

    assert capture_sentry_session_rows(sink, memory, minutes=60, state=state) == 1
    assert len(sink.captured) == 2
