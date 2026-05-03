from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()
LEGACY_CONFIG_PATH = HOME / ".config/agent-sentry/env"
CONFIG_PATH = HOME / ".config/agent-vm-observability/env"
LEGACY_STATE_PATH = HOME / ".local/state/agent-sentry/state.json"
STATE_PATH = HOME / ".local/state/agent-vm-observability/state.json"
MEMORY_DB_PATH = HOME / ".agent-vm-observability/memory.db"
CODEX_LOGS_DB = HOME / ".codex/logs_2.sqlite"
CODEX_STATE_DB = HOME / ".codex/state_5.sqlite"
CLAUDE_PROJECTS_GLOB = str(HOME / ".claude/projects/**/*.jsonl")
CLAUDE_MEM_DB = HOME / ".claude-mem/claude-mem.db"


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, raw_value = stripped.split("=", 1)
    key = key.strip().removeprefix("export ").strip()
    if not key:
        return None
    try:
        parts = shlex.split(f"x={raw_value.strip()}")
        value = parts[0].split("=", 1)[1] if parts else ""
    except Exception:
        value = raw_value.strip().strip("\"'")
    return key, value


def load_env_files() -> None:
    """Load legacy and package config files without executing shell code."""
    seen: set[Path] = set()
    for env_name, default_path in (("AGENT_SENTRY_CONFIG", LEGACY_CONFIG_PATH), ("AGENT_VM_CONFIG", CONFIG_PATH)):
        path = env_path(env_name, default_path)
        if path in seen or not path.exists():
            continue
        seen.add(path)
        for line in path.read_text().splitlines():
            parsed = _parse_env_line(line)
            if parsed:
                key, value = parsed
                os.environ[key] = value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


@dataclass(frozen=True)
class RuntimeConfig:
    config_path: Path
    legacy_config_path: Path
    state_path: Path
    memory_db_path: Path
    codex_logs_db: Path
    codex_state_db: Path
    claude_projects_glob: str
    claude_mem_db: Path
    sentry_dsn: str | None
    sentry_org: str
    sentry_project: str
    sentry_project_id: str | None
    include_text: bool
    traces_sample_rate: float
    max_batch: int
    poll_seconds: int
    record_memory: bool


def get_config() -> RuntimeConfig:
    return RuntimeConfig(
        config_path=env_path("AGENT_VM_CONFIG", CONFIG_PATH),
        legacy_config_path=env_path("AGENT_SENTRY_CONFIG", LEGACY_CONFIG_PATH),
        state_path=env_path("AGENT_VM_STATE", env_path("AGENT_SENTRY_STATE", STATE_PATH)),
        memory_db_path=env_path("AGENT_VM_MEMORY_DB", MEMORY_DB_PATH),
        codex_logs_db=env_path("AGENT_VM_CODEX_LOGS_DB", env_path("AGENT_SENTRY_CODEX_LOGS_DB", CODEX_LOGS_DB)),
        codex_state_db=env_path("AGENT_VM_CODEX_STATE_DB", env_path("AGENT_SENTRY_CODEX_STATE_DB", CODEX_STATE_DB)),
        claude_projects_glob=os.environ.get("AGENT_VM_CLAUDE_GLOB") or os.environ.get("AGENT_SENTRY_CLAUDE_GLOB") or CLAUDE_PROJECTS_GLOB,
        claude_mem_db=env_path("AGENT_VM_CLAUDE_MEM_DB", CLAUDE_MEM_DB),
        sentry_dsn=os.environ.get("SENTRY_DSN") or os.environ.get("AGENT_SENTRY_DSN"),
        sentry_org=os.environ.get("SENTRY_ORG", ""),
        sentry_project=os.environ.get("SENTRY_PROJECT", "agent-vm-usage"),
        sentry_project_id=os.environ.get("SENTRY_PROJECT_ID"),
        include_text=env_bool("AGENT_VM_INCLUDE_TEXT", env_bool("AGENT_SENTRY_INCLUDE_TEXT", False)),
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        max_batch=env_int("AGENT_VM_MAX_BATCH", env_int("AGENT_SENTRY_MAX_BATCH", 250)),
        poll_seconds=env_int("AGENT_VM_POLL_SECONDS", env_int("AGENT_SENTRY_POLL_SECONDS", 15)),
        record_memory=env_bool("AGENT_VM_RECORD_MEMORY", True),
    )

