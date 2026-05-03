import json
import sqlite3
from pathlib import Path

from agent_vm_observability.config import RuntimeConfig
from agent_vm_observability.ingest import AgentIngestor, claude_record_to_traces, codex_log_to_trace, parse_codex_kv, pi_record_to_traces
from agent_vm_observability.model import GitMetadataCache


class CapturingSink:
    dry_run = False

    def __init__(self) -> None:
        self.traces = []

    def capture(self, trace) -> None:
        self.traces.append(trace)


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
    assert trace.duration_ms == 9
    assert trace.measurements["estimated_bytes"] == 120
    assert trace.token_usage["input_tokens"] == 10
    assert trace.token_usage["cache_read_input_tokens"] == 100
    assert trace.token_usage["output_tokens"] == 5
    assert trace.measurements["cost_usd"] == 0.000125
    assert trace.measurements["total_tokens"] == 115
    assert trace.extra["body"]["value_type"] == "str"
    assert "codex.websocket_event" not in json.dumps(trace.extra["body"])


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


def test_claude_tool_result_omits_raw_text_by_default() -> None:
    record = {
        "type": "user",
        "sessionId": "session-1",
        "uuid": "uuid-2",
        "cwd": "/Users/example/3_zonko_projects/example",
        "timestamp": "2026-04-20T11:00:01Z",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
        "toolUseResult": {"stdout": "PRIVATE_DOC_CONTENT_SHOULD_NOT_LEAVE", "exit_code": 0},
    }
    trace = claude_record_to_traces(record, GitMetadataCache(), include_text=False)[0]
    payload = json.dumps(trace.extra, sort_keys=True)
    assert "PRIVATE_DOC_CONTENT_SHOULD_NOT_LEAVE" not in payload
    assert trace.extra["tool_use_result"]["value_type"] == "dict"
    assert trace.extra["tool_use_result"]["json_hash"]


def test_pi_record_to_traces_extracts_usage_and_tool_calls() -> None:
    header = {"type": "session", "version": 3, "id": "pi-session-1", "cwd": "/Users/example/3_zonko_projects/example"}
    record = {
        "type": "message",
        "id": "entry-1",
        "parentId": "parent-1",
        "timestamp": "2026-04-20T11:00:00Z",
        "message": {
            "role": "assistant",
            "api": "anthropic-messages",
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "stopReason": "toolUse",
            "usage": {
                "input": 3,
                "output": 4,
                "cacheRead": 5,
                "cacheWrite": 6,
                "totalTokens": 18,
                "cost": {"input": 0.1, "output": 0.2, "cacheRead": 0.03, "cacheWrite": 0.04, "total": 0.37},
            },
            "content": [{"type": "toolCall", "id": "tool-1", "name": "read", "arguments": {"path": "secret.txt"}}],
        },
    }

    traces = pi_record_to_traces(record, header, GitMetadataCache(), include_text=False)

    assert [trace.kind for trace in traces] == ["assistant_turn", "tool_use"]
    assert traces[0].agent == "pi"
    assert traces[0].project == "example"
    assert traces[0].token_usage["input_tokens"] == 3
    assert traces[0].token_usage["cache_read_input_tokens"] == 5
    assert traces[0].token_usage["cache_creation_input_tokens"] == 6
    assert traces[0].measurements["cost_usd"] == 0.37
    assert traces[1].tool_name == "read"
    assert traces[1].extra["tool"]["input"] is None


def test_pi_tool_result_omits_raw_details_by_default() -> None:
    header = {"type": "session", "version": 3, "id": "pi-session-1", "cwd": "/Users/example/3_zonko_projects/example"}
    record = {
        "type": "message",
        "id": "entry-2",
        "timestamp": "2026-04-20T11:00:01Z",
        "message": {
            "role": "toolResult",
            "toolCallId": "tool-1",
            "toolName": "bash",
            "isError": False,
            "details": {"stdout": "PRIVATE_PI_TOOL_OUTPUT"},
            "content": [{"type": "text", "text": "PRIVATE_PI_TOOL_OUTPUT"}],
        },
    }

    trace = pi_record_to_traces(record, header, GitMetadataCache(), include_text=False)[0]
    payload = json.dumps(trace.extra, sort_keys=True)

    assert "PRIVATE_PI_TOOL_OUTPUT" not in payload
    assert trace.extra["details"]["value_type"] == "dict"
    assert trace.extra["content_summary"]["text_hash"]


def test_pi_subagent_result_omits_raw_progress_by_default() -> None:
    header = {"type": "session", "version": 3, "id": "pi-session-1", "cwd": "/Users/example/3_zonko_projects/example"}
    record = {
        "type": "message",
        "id": "entry-3",
        "timestamp": "2026-04-20T11:00:02Z",
        "message": {
            "role": "toolResult",
            "toolCallId": "tool-1",
            "toolName": "subagent",
            "isError": False,
            "content": [{"type": "text", "text": "done"}],
            "details": {
                "mode": "single",
                "results": [
                    {
                        "agent": "worker",
                        "task": "PRIVATE_SUBAGENT_TASK",
                        "exitCode": 0,
                        "model": "claude-haiku-4-5",
                        "progressSummary": {"notes": "PRIVATE_PROGRESS_TEXT", "durationMs": 12},
                    }
                ],
            },
        },
    }

    traces = pi_record_to_traces(record, header, GitMetadataCache(), include_text=False)
    subagent_trace = next(trace for trace in traces if trace.kind == "subagent_run")
    payload = json.dumps(subagent_trace.extra, sort_keys=True)

    assert "PRIVATE_SUBAGENT_TASK" not in payload
    assert "PRIVATE_PROGRESS_TEXT" not in payload
    assert subagent_trace.extra["progress_summary"]["value_type"] == "dict"


def test_codex_threads_paginates_duplicate_updated_at(tmp_path: Path) -> None:
    codex_state = tmp_path / "state.sqlite"
    conn = sqlite3.connect(codex_state)
    conn.execute(
        """
        create table threads(id text, updated_at_ms integer, created_at_ms integer, source text,
        model_provider text, cwd text, title text, tokens_used integer, first_user_message text,
        model text, reasoning_effort text, cli_version text, git_branch text, git_sha text,
        git_origin_url text)
        """
    )
    for thread_id in ("t0", "t1", "t2"):
        conn.execute(
            "insert into threads values (?, 1000, 1, 'cli', 'openai', ?, '', 0, '', 'gpt-5.4', '', '1', '', '', '')",
            (thread_id, str(tmp_path)),
        )
    conn.commit()
    conn.close()

    config = RuntimeConfig(
        config_path=tmp_path / "env",
        legacy_config_path=tmp_path / "legacy-env",
        state_path=tmp_path / "bridge-state.json",
        memory_db_path=tmp_path / "memory.db",
        codex_logs_db=tmp_path / "missing-logs.sqlite",
        codex_state_db=codex_state,
        claude_projects_glob=str(tmp_path / "missing-claude.jsonl"),
        pi_sessions_glob=str(tmp_path / "missing-pi.jsonl"),
        sentry_dsn=None,
        sentry_org="org",
        sentry_project="project",
        sentry_project_id=None,
        include_text=False,
        traces_sample_rate=1.0,
        max_batch=2,
        poll_seconds=1,
        record_memory=False,
    )
    sink = CapturingSink()
    ingestor = AgentIngestor(config, sink, None)
    state = {"codex_threads_last_updated_ms": 0}

    assert ingestor.process_codex_threads(state, max_batch=2) == 2
    assert [trace.session_id for trace in sink.traces] == ["t0", "t1"]
    assert state["codex_threads_last_updated_ms"] == 1000
    assert state["codex_threads_last_id"] == "t1"

    assert ingestor.process_codex_threads(state, max_batch=2) == 1
    assert [trace.session_id for trace in sink.traces] == ["t0", "t1", "t2"]
