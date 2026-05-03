import json
from datetime import datetime, timezone
from pathlib import Path

from agent_vm_observability.memory import MemoryStore
from agent_vm_observability.model import NormalizedTrace


def test_search_and_context_read_generic_memories(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.initialize()
    with store.connect() as conn:
        with conn:
            conn.execute(
                """
                insert into memories(source, source_id, agent, source_session_id, project, scope, type, title, body,
                  confidence, status, evidence_json, created_at, created_at_epoch, content_hash)
                values (?, ?, ?, ?, ?, 'project', ?, ?, ?, 0.8, 'active', ?, ?, ?, ?)
                """,
                (
                    "test",
                    "memory-1",
                    "pi",
                    "session-1",
                    "example",
                    "decision",
                    "Dashboard decision",
                    "Use Sentry plus the local SQLite dashboard.",
                    json.dumps({"source": "test"}),
                    "2026-04-20T11:02:00Z",
                    1776682920,
                    "hash-1",
                ),
            )
            store.rebuild_fts(conn)

    rows = store.search("dashboard", limit=5)
    assert rows
    context = store.context("/Users/example/3_zonko_projects/example", "pi")
    assert "Dashboard decision" in context


def test_usage_rollup_dedupes_claude_assistant_message_ids(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    now = datetime.now(timezone.utc)
    for source_id in ("claude-a", "claude-b"):
        store.record_trace(
            NormalizedTrace(
                agent="claude-code",
                kind="assistant_turn",
                timestamp=now,
                source_event_id=source_id,
                session_id="session-1",
                model="claude-opus-4-7",
                project="example",
                token_usage={"input_tokens": 10, "output_tokens": 5},
                measurements={"cost_usd": 0.001},
                extra={"message_id": "message-1"},
            )
        )

    store.record_trace(
        NormalizedTrace(
            agent="codex",
            kind="codex.sse_event",
            timestamp=now,
            source_event_id="codex-1",
            session_id="session-2",
            model="gpt-5.4",
            project="example",
            token_usage={"input_tokens": 20, "output_tokens": 4},
            measurements={"cost_usd": 0.002},
        )
    )

    rollup = store.usage_rollup(hours=24, top_n=5)
    assert rollup["totals"]["usage_events"] == 2
    assert rollup["totals"]["input_tokens"] == 30
    assert rollup["totals"]["output_tokens"] == 9
    assert rollup["totals"]["cost_usd"] == 0.003
