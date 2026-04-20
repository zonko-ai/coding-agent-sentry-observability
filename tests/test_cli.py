from agent_vm_observability.cli import main


def test_self_test_dry_run_smoke(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_VM_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_VM_STATE", str(tmp_path / "state.json"))
    assert main(["self-test", "--dry-run"]) == 0


def test_status_smoke(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_VM_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_VM_STATE", str(tmp_path / "state.json"))
    assert main(["status"]) == 0

