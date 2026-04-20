import sqlite3
from pathlib import Path

from agent_vm_observability.ingest import claude_record_to_traces, codex_log_to_trace, parse_codex_kv
from agent_vm_observability.model import GitMetadataCache


def test_parse_codex_kv_handles_quoted_values() -> None:
    parsed = parse_codex_kv('event.name="codex.websocket_event" model="gpt-5.4" success=true duration_ms=42')
    assert parsed["event.name"] == "codex.websocket_event"
    assert parsed["model"] == "gpt-5.4"
    assert parsed["success"] == "true"
    assert parsed["duration_ms"] == "42"


def test_codex_log_to_trace_normalizes_fields(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        create table logs(id integer, ts integer, ts_nanos integer, level text, target text,
        feedback_log_body text, module_path text, file text, line integer, thread_id text,
        process_uuid text, estimated_bytes integer)
        """
    )
    body = f'event.name="codex.websocket_event" model="gpt-5.4" cwd="{tmp_path}" success=true duration_ms=9'
    conn.execute(
        "insert into logs values (1, 1776683000, 0, 'INFO', 'codex_otel.log_only', ?, 'm', 'f', 1, 'thread-1', 'p', 120)",
        (body,),
    )
    row = conn.execute("select * from logs").fetchone()
    trace = codex_log_to_trace(row, GitMetadataCache())
    assert trace.agent == "codex"
    assert trace.kind == "codex.websocket_event"
    assert trace.title == "codex.codex.websocket_event"
    assert trace.session_id == "thread-1"
    assert trace.model == "gpt-5.4"
    assert trace.duration_ms == 9
    assert trace.measurements["estimated_bytes"] == 120


def test_claude_record_to_traces_extracts_assistant_and_tool() -> None:
    record = {
        "type": "assistant",
        "sessionId": "session-1",
        "uuid": "uuid-1",
        "cwd": "/Users/example/3_zonko_projects/example",
        "version": "2.1.114",
        "timestamp": "2026-04-20T11:00:00Z",
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-7",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 3, "output_tokens": 4},
            "content": [{"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "x"}}],
        },
    }
    traces = claude_record_to_traces(record, GitMetadataCache(), include_text=False)
    assert [trace.kind for trace in traces] == ["assistant_turn", "tool_use"]
    assert traces[0].agent == "claude-code"
    assert traces[0].project == "example"
    assert traces[0].token_usage["input_tokens"] == 3
    assert traces[1].tool_name == "Read"
