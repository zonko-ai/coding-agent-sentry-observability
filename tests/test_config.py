import os

from agent_vm_observability import config as config_module
from agent_vm_observability.config import get_config, load_env_files


def test_config_defaults_are_project_neutral(monkeypatch) -> None:
    monkeypatch.delenv("SENTRY_ORG", raising=False)
    monkeypatch.delenv("SENTRY_PROJECT", raising=False)
    monkeypatch.delenv("SENTRY_PROJECT_ID", raising=False)

    config = get_config()

    assert config.sentry_org == ""
    assert config.sentry_project == "agent-vm-usage"
    assert config.pi_suggester_glob.endswith(".pi/suggester")


def test_load_env_files_preserves_shell_env_and_prefers_new_config(monkeypatch, tmp_path) -> None:
    legacy_path = tmp_path / "legacy.env"
    config_path = tmp_path / "config.env"
    legacy_path.write_text("SENTRY_PROJECT=legacy-project\nSENTRY_ORG=legacy-org\n")
    config_path.write_text("SENTRY_PROJECT=config-project\nSENTRY_ORG=config-org\n")
    monkeypatch.setattr(config_module, "LEGACY_CONFIG_PATH", legacy_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)
    monkeypatch.setenv("SENTRY_PROJECT", "shell-project")
    monkeypatch.delenv("SENTRY_ORG", raising=False)

    load_env_files()

    assert os.environ["SENTRY_PROJECT"] == "shell-project"
    assert os.environ["SENTRY_ORG"] == "config-org"
