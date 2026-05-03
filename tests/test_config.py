import os

from agent_vm_observability import config as config_module


def test_load_env_files_honors_custom_config_path(monkeypatch, tmp_path) -> None:
    custom = tmp_path / "custom.env"
    custom.write_text("AGENT_VM_POLL_SECONDS=7\n")
    monkeypatch.setattr(config_module, "LEGACY_CONFIG_PATH", tmp_path / "missing-legacy.env")
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "missing-default.env")
    monkeypatch.setenv("AGENT_VM_CONFIG", str(custom))
    monkeypatch.delenv("AGENT_VM_POLL_SECONDS", raising=False)

    config_module.load_env_files()

    assert os.environ["AGENT_VM_POLL_SECONDS"] == "7"
    assert config_module.get_config().poll_seconds == 7


def test_agent_vm_include_text_overrides_legacy(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SENTRY_INCLUDE_TEXT", "1")
    monkeypatch.setenv("AGENT_VM_INCLUDE_TEXT", "0")

    assert config_module.get_config().include_text is False
