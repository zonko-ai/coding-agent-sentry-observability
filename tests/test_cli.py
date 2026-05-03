import json
from datetime import datetime, timezone

from agent_vm_observability import config as config_module
from agent_vm_observability.cli import main
from agent_vm_observability.memory import MemoryStore


def test_self_test_dry_run_smoke(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_VM_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_VM_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("AGENT_VM_PI_GLOB", str(tmp_path / "missing-pi.jsonl"))
    assert main(["self-test", "--dry-run"]) == 0


def test_status_smoke(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_VM_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_VM_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("AGENT_VM_PI_GLOB", str(tmp_path / "missing-pi.jsonl"))
    assert main(["status"]) == 0


def test_bridge_records_local_memory_without_sentry(monkeypatch, tmp_path) -> None:
    claude_jsonl = tmp_path / "claude.jsonl"
    claude_jsonl.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "session-1",
                "uuid": "uuid-1",
                "cwd": str(tmp_path),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-7",
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                    "content": "hello",
                },
            }
        )
        + "\n"
    )
    monkeypatch.setattr(config_module, "LEGACY_CONFIG_PATH", tmp_path / "missing-legacy.env")
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "missing-default.env")
    monkeypatch.delenv("AGENT_VM_CONFIG", raising=False)
    monkeypatch.delenv("AGENT_SENTRY_CONFIG", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("AGENT_SENTRY_DSN", raising=False)
    monkeypatch.setenv("AGENT_VM_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_VM_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("AGENT_VM_CODEX_LOGS_DB", str(tmp_path / "missing-logs.sqlite"))
    monkeypatch.setenv("AGENT_VM_CODEX_STATE_DB", str(tmp_path / "missing-state.sqlite"))
    monkeypatch.setenv("AGENT_VM_CLAUDE_GLOB", str(claude_jsonl))
    monkeypatch.setenv("AGENT_VM_PI_GLOB", str(tmp_path / "missing-pi.jsonl"))

    assert main(["bridge", "--once", "--backfill-minutes", "60"]) == 0
    assert MemoryStore(tmp_path / "memory.db").counts()["events"] == 1

