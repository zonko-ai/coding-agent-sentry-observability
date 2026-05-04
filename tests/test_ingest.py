import sqlite3
from pathlib import Path

from agent_vm_observability.ingest import claude_record_to_traces, codex_log_to_trace, parse_codex_kv, pi_event_to_trace
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
    body = (
        f'event.name="codex.websocket_event" model="gpt-5.4" cwd="{tmp_path}" success=true duration_ms=9 '
        'tool_name=exec_command '
        'input_token_count=110 cached_token_count=100 output_token_count=5 reasoning_token_count=2 tool_token_count=115'
    )
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
    assert trace.tool_name == "exec_command"
    assert trace.tool_kind == "codex_tool"
    assert trace.duration_ms == 9
    assert trace.measurements["estimated_bytes"] == 120
    assert trace.token_usage["input_tokens"] == 10
    assert trace.token_usage["cache_read_input_tokens"] == 100
    assert trace.token_usage["output_tokens"] == 5
    assert trace.measurements["cost_usd"] == 0.000125
    assert trace.measurements["total_tokens"] == 115


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
            "usage": {
                "input_tokens": 3,
                "output_tokens": 4,
                "cache_creation_input_tokens": 6,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 6},
            },
            "content": [{"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "x"}}],
        },
    }
    traces = claude_record_to_traces(record, GitMetadataCache(), include_text=False)
    assert [trace.kind for trace in traces] == ["assistant_turn", "tool_use"]
    assert traces[0].agent == "claude-code"
    assert traces[0].project == "example"
    assert traces[0].token_usage["input_tokens"] == 3
    assert traces[0].token_usage["cache_creation_1h_input_tokens"] == 6
    assert traces[0].measurements["cost_usd"] == 0.000175
    assert traces[1].tool_name == "Read"


def test_pi_event_to_trace_normalizes_suggestion_usage(tmp_path: Path) -> None:
    root = tmp_path / ".pi" / "suggester"
    record = {
        "at": "2026-05-03T05:55:11.503Z",
        "level": "info",
        "message": "suggestion.generated",
        "meta": {
            "turnId": "08b72983",
            "variantName": "default",
            "strategy": "compact",
            "latencyMs": 10880,
            "suggestionChars": 52,
            "inputTokens": 1617,
            "outputTokens": 441,
            "cacheReadTokens": 12,
            "cacheWriteTokens": 0,
            "totalTokens": 2070,
            "cost": 0.021315,
            "preview": "Can you add GitHub Actions CI for tests and linting?",
        },
    }

    trace = pi_event_to_trace(record, root, GitMetadataCache(), include_text=False, source_event_id="pi-log:1")

    assert trace.agent == "pi"
    assert trace.kind == "suggestion.generated"
    assert trace.title == "pi.suggestion.generated"
    assert trace.project == tmp_path.name
    assert trace.cwd == str(tmp_path)
    assert trace.turn_id == "08b72983"
    assert trace.duration_ms == 10880
    assert trace.success is True
    assert trace.token_usage["input_tokens"] == 1617
    assert trace.token_usage["output_tokens"] == 441
    assert trace.token_usage["cache_read_input_tokens"] == 12
    assert trace.measurements["total_tokens"] == 2070
    assert trace.measurements["cost_usd"] == 0.021315
    assert trace.extra["meta"]["preview_len"] == 52
    assert "preview" not in trace.extra["meta"]
