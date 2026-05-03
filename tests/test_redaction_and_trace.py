from agent_vm_observability.model import NormalizedTrace
from agent_vm_observability.redaction import redact_text, scrub


def test_redacts_common_secret_shapes() -> None:
    text = "OPENAI_API_KEY=sk-1234567890abcdef password=hunter2 user=user@example.com"
    redacted = redact_text(text)
    assert "sk-1234567890abcdef" not in redacted
    assert "hunter2" not in redacted
    assert "user@example.com" not in redacted
    assert "[redacted" in redacted


def test_scrub_redacts_secret_keys() -> None:
    value = scrub({"token": "abc", "nested": {"password": "pw"}, "safe": "ok"})
    assert value["token"] == "[redacted]"
    assert value["nested"]["password"] == "[redacted]"
    assert value["safe"] == "ok"


def test_trace_keeps_claude_sentry_title_compatible() -> None:
    trace = NormalizedTrace(agent="claude-code", kind="assistant_turn", session_id="s1", model="claude-opus")
    assert trace.title == "claude.assistant_turn"
    tags = trace.sentry_tags()
    assert tags["claude.agent"] == "claude-code"
    assert tags["claude.session_id"] == "s1"

