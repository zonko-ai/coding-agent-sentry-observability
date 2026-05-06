from agent_vm_observability.model import NormalizedTrace
from agent_vm_observability.config import RuntimeConfig
from agent_vm_observability.sentry_sink import USAGE_SCHEMA, SentrySink, _gen_ai_attributes, _measurement_name, _transaction_op


def test_gen_ai_attributes_include_token_and_cost_fields() -> None:
    trace = NormalizedTrace(agent="codex", kind="assistant_turn", model="gpt-5.5", provider="openai")
    measurements = {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "cost_usd": 0.00123,
    }

    attrs = _gen_ai_attributes(trace, measurements)

    assert "gen_ai.request.model" not in attrs
    assert "gen_ai.response.model" not in attrs
    assert attrs["gen_ai.operation.name"] == "invoke_agent"
    assert attrs["gen_ai.usage.input_tokens"] == 100
    assert attrs["gen_ai.usage.output_tokens"] == 20
    assert attrs["gen_ai.usage.total_tokens"] == 120
    assert attrs["gen_ai.cost.total_tokens"] == 0.00123
    assert _measurement_name("cost_usd") == "cost_usd"


def test_non_usage_traces_do_not_get_llm_model_attributes() -> None:
    trace = NormalizedTrace(agent="codex", kind="codex.websocket_event", model="gpt-5.5")

    attrs = _gen_ai_attributes(trace, {})

    assert "gen_ai.request.model" not in attrs
    assert "gen_ai.response.model" not in attrs
    assert attrs["gen_ai.operation.name"] == "codex.websocket_event"


def test_tool_traces_do_not_get_llm_model_attributes() -> None:
    trace = NormalizedTrace(agent="codex", kind="tool_call", model="gpt-5.5", tool_name="read")

    attrs = _gen_ai_attributes(trace, {})

    assert "gen_ai.request.model" not in attrs
    assert "gen_ai.response.model" not in attrs
    assert attrs["gen_ai.operation.name"] == "execute_tool"


def test_session_traces_use_agent_session_op() -> None:
    trace = NormalizedTrace(agent="codex", kind="session_v9", tags={"usage_rollup": "session"})

    attrs = _gen_ai_attributes(trace, {})

    assert _transaction_op(trace) == "gen_ai.agent.session"
    assert attrs["gen_ai.operation.name"] == "agent_session"


def test_cost_or_token_traces_with_model_use_ai_invocation_op() -> None:
    trace = NormalizedTrace(agent="pi", kind="suggestion.generated", model="gpt-5.5", measurements={"cost_usd": 0.01})

    assert _transaction_op(trace) == "gen_ai.invoke_agent"


def test_usage_traces_are_tagged_with_usage_schema(tmp_path) -> None:
    sink = SentrySink(
        RuntimeConfig(
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
        ),
        dry_run=True,
    )
    trace = NormalizedTrace(agent="pi", kind="usage_v7", model="gpt-5.5", measurements={"cost_usd": 0.01}, tags={"usage_rollup": "event"})

    sink.capture(trace)

    assert sink.captured[0].tags["usage_schema"] == USAGE_SCHEMA
    assert sink.captured[0].tags["usage_canonical"] == "true"
    assert sink.captured[0].tags["usage_model"] == "gpt-5.5"
    assert sink.captured[0].tags["usage_rollup"] == "event"


def test_live_usage_traces_are_marked_as_canonical_events(tmp_path) -> None:
    sink = SentrySink(
        RuntimeConfig(
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
        ),
        dry_run=True,
    )
    trace = NormalizedTrace(agent="pi", kind="suggestion.generated", model="gpt-5.5", measurements={"cost_usd": 0.01})

    sink.capture(trace)

    assert sink.captured[0].tags["usage_schema"] == USAGE_SCHEMA
    assert sink.captured[0].tags["usage_canonical"] == "true"
    assert sink.captured[0].tags["usage_rollup"] == "event"


def test_session_traces_are_tagged_with_usage_schema(tmp_path) -> None:
    sink = SentrySink(
        RuntimeConfig(
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
        ),
        dry_run=True,
    )
    trace = NormalizedTrace(agent="pi", kind="session_v9", session_id="session-1", model="gpt-5.5", tags={"usage_rollup": "session"})

    sink.capture(trace)

    assert sink.captured[0].tags["usage_schema"] == USAGE_SCHEMA
    assert sink.captured[0].tags["usage_canonical"] == "true"
    assert sink.captured[0].tags["usage_rollup"] == "session"
    assert sink.captured[0].tags["usage_model"] == "gpt-5.5"


def test_cost_or_token_traces_without_model_use_ai_invocation_op() -> None:
    trace = NormalizedTrace(agent="pi", kind="suggestion.generated", measurements={"cost_usd": 0.01})

    assert _transaction_op(trace) == "gen_ai.invoke_agent"
