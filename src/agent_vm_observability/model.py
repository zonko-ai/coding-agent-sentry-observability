from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .redaction import safe_path_tag_value, safe_tag_value, scrub, short_hash
from .timeutil import to_timestamp


def infer_project(cwd: str | None) -> str | None:
    if not cwd:
        return None
    path = Path(cwd).expanduser()
    parts = path.parts
    for marker in ("3_zonko_projects", "4._personal_projects", "5._discard_repos", "1._public_repos"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return path.name if path.name else None


class GitMetadataCache:
    def __init__(self) -> None:
        self._cache: dict[str, dict[str, str | None]] = {}

    def for_cwd(self, cwd: str | None) -> dict[str, str | None]:
        if not cwd:
            return {"repo": None, "git_branch": None, "git_sha": None}
        path = Path(cwd).expanduser()
        key = str(path)
        if key in self._cache:
            return self._cache[key]

        repo_root = None
        for parent in (path, *path.parents):
            if (parent / ".git").exists():
                repo_root = parent
                break
        if not repo_root:
            value = {"repo": None, "git_branch": None, "git_sha": None}
            self._cache[key] = value
            return value

        branch = None
        sha = None
        git_path = repo_root / ".git"
        head_path = git_path / "HEAD"
        try:
            head = head_path.read_text().strip()
            if head.startswith("ref:"):
                ref = head.split(" ", 1)[1]
                branch = ref.removeprefix("refs/heads/")
                ref_path = git_path / ref
                if ref_path.exists():
                    sha = ref_path.read_text().strip()
            else:
                sha = head
        except Exception:
            pass

        value = {"repo": str(repo_root), "git_branch": branch, "git_sha": sha}
        self._cache[key] = value
        return value


@dataclass
class NormalizedTrace:
    agent: str
    kind: str
    timestamp: datetime | None = None
    level: str = "info"
    source_event_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    project: str | None = None
    cwd: str | None = None
    repo: str | None = None
    git_branch: str | None = None
    git_sha: str | None = None
    model: str | None = None
    provider: str | None = None
    agent_version: str | None = None
    tool_name: str | None = None
    tool_kind: str | None = None
    command_kind: str | None = None
    duration_ms: float | None = None
    success: bool | None = None
    exit_code: int | None = None
    token_usage: dict[str, int | float] = field(default_factory=dict)
    measurements: dict[str, int | float] = field(default_factory=dict)
    tags: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def sentry_source(self) -> str:
        return "claude" if self.agent == "claude-code" else self.agent

    @property
    def title(self) -> str:
        return f"{self.sentry_source}.{self.kind}"

    def stable_event_id(self) -> str:
        if self.source_event_id:
            return self.source_event_id
        payload = json.dumps(
            {
                "agent": self.agent,
                "kind": self.kind,
                "timestamp": to_timestamp(self.timestamp),
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "tool_name": self.tool_name,
                "extra": self.extra,
            },
            sort_keys=True,
            default=str,
        )
        return short_hash(payload) or f"{self.agent}:{self.kind}"

    def all_measurements(self) -> dict[str, int | float]:
        merged: dict[str, int | float] = {}
        merged.update(self.token_usage)
        merged.update(self.measurements)
        if self.duration_ms is not None:
            merged["duration_ms"] = self.duration_ms
        if self.exit_code is not None:
            merged["exit_code"] = self.exit_code
        return merged

    def sentry_tags(self) -> dict[str, str]:
        base: dict[str, Any] = {
            "agent": self.agent,
            "kind": self.kind,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "project": self.project,
            "cwd": self.cwd,
            "repo": self.repo,
            "git_branch": self.git_branch,
            "git_sha": self.git_sha,
            "model": self.model,
            "provider": self.provider,
            "agent_version": self.agent_version,
            "tool_name": self.tool_name,
            "tool_kind": self.tool_kind,
            "command_kind": self.command_kind,
            "success": self.success,
            "vm_host": socket.gethostname(),
        }
        base.update(self.tags)
        alias_keys = {
            "agent": "agent",
            "kind": "kind",
            "session_id": "session_id",
            "turn_id": "turn_id",
            "project": "agent_project",
            "cwd": "agent_cwd",
            "repo": "agent_repo",
            "git_branch": "git_branch",
            "git_sha": "git_sha",
            "model": "agent_model",
            "provider": "provider",
            "agent_version": "agent_version",
            "tool_name": "tool_name",
            "tool_kind": "tool_kind",
            "command_kind": "command_kind",
            "success": "success",
            "vm_host": "vm_host",
        }
        tags = {}
        for key, value in base.items():
            if value in (None, ""):
                continue
            formatter = safe_path_tag_value if key in {"cwd", "repo"} else safe_tag_value
            tags[f"{self.sentry_source}.{key}"] = formatter(value)
        for key, alias in alias_keys.items():
            value = base.get(key)
            if value not in (None, ""):
                formatter = safe_path_tag_value if key in {"cwd", "repo"} else safe_tag_value
                tags[alias] = formatter(value)
        return tags

    def sentry_extra(self) -> dict[str, Any]:
        return scrub(
            {
                "normalized_trace": {
                    "agent": self.agent,
                    "kind": self.kind,
                    "timestamp": to_timestamp(self.timestamp),
                    "source_event_id": self.stable_event_id(),
                    "session_id": self.session_id,
                    "turn_id": self.turn_id,
                    "trace_id": self.trace_id,
                    "span_id": self.span_id,
                    "parent_span_id": self.parent_span_id,
                    "project": self.project,
                    "cwd": self.cwd,
                    "repo": self.repo,
                    "model": self.model,
                    "tool_name": self.tool_name,
                    "success": self.success,
                    "measurements": self.all_measurements(),
                },
                **self.extra,
            }
        )


def normalize_level(level: str | None) -> str:
    normalized = (level or "info").lower()
    if normalized in {"warn", "warning"}:
        return "warning"
    if normalized in {"fatal", "error", "info", "debug"}:
        return normalized
    if normalized in {"trace", "verbose"}:
        return "debug"
    return "info"
