from __future__ import annotations

import glob
import json
import os
import re
import shlex
import sqlite3
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RuntimeConfig, env_int
from .memory import MemoryStore
from .model import GitMetadataCache, NormalizedTrace, infer_project
from .pricing import apply_cost_estimate
from .redaction import redact_text, scrub, short_hash
from .sentry_sink import SentrySink
from .state import empty_state
from .timeutil import parse_timestamp, to_timestamp, utc_now


def log(message: str) -> None:
    print(f"{utc_now().isoformat(timespec='seconds')} {message}", flush=True)


def sqlite_connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    uri = f"file:{path}?mode=ro&cache=shared"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def parse_codex_kv(body: str | None) -> dict[str, str]:
    if not body:
        return {}
    matches = re.findall(r"([\w][\w.\-]*)=(\"(?:[^\"\\]|\\.)*\"|[^\s}]+)", body)
    result: dict[str, str] = {}
    for key, raw_value in matches:
        value = raw_value
        if value.startswith('"') and value.endswith('"'):
            try:
                value = shlex.split(f"x={value}")[0].split("=", 1)[1]
            except Exception:
                value = value[1:-1]
        result[key] = value
    return result


def flatten_usage(usage: dict[str, Any] | None) -> dict[str, int | float]:
    if not isinstance(usage, dict):
        return {}
    keys = ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]
    measurements: dict[str, int | float] = {}
    for key in keys:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            measurements[key] = value
    server_tool_use = usage.get("server_tool_use")
    if isinstance(server_tool_use, dict):
        for key, value in server_tool_use.items():
            if isinstance(value, (int, float)):
                measurements[f"server_tool_use.{key}"] = value
    cache_creation = usage.get("cache_creation")
    if isinstance(cache_creation, dict):
        for source_key, target_key in (
            ("ephemeral_5m_input_tokens", "cache_creation_5m_input_tokens"),
            ("ephemeral_1h_input_tokens", "cache_creation_1h_input_tokens"),
        ):
            value = cache_creation.get(source_key)
            if isinstance(value, (int, float)):
                measurements[target_key] = value
    return measurements


def content_summary(content: Any, include_text: bool) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "content_type": type(content).__name__,
        "text_len": 0,
        "content_types": [],
        "tool_uses": [],
        "tool_results": 0,
    }
    text_chunks: list[str] = []
    if isinstance(content, str):
        summary["text_len"] = len(content)
        if include_text:
            summary["text"] = redact_text(content)
        else:
            summary["text_hash"] = short_hash(content)
        return summary

    if isinstance(content, list):
        content_types: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        tool_results = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", "unknown"))
            content_types.append(block_type)
            if block_type == "text":
                text = str(block.get("text", ""))
                summary["text_len"] = int(summary["text_len"]) + len(text)
                text_chunks.append(text)
            elif block_type == "thinking":
                thinking = str(block.get("thinking", ""))
                summary["thinking_len"] = int(summary.get("thinking_len", 0)) + len(thinking)
            elif block_type in {"tool_use", "toolCall"}:
                tool_input = block.get("input") if block_type == "tool_use" else block.get("arguments")
                tool_uses.append(
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input_keys": sorted(tool_input.keys()) if isinstance(tool_input, dict) else [],
                        "input": scrub(tool_input) if include_text else None,
                    }
                )
            elif block_type == "tool_result":
                tool_results += 1
                result_text = block.get("content")
                if isinstance(result_text, str):
                    summary["tool_result_text_len"] = int(summary.get("tool_result_text_len", 0)) + len(result_text)
        summary["content_types"] = sorted(set(content_types))
        summary["tool_uses"] = [tool for tool in tool_uses if tool.get("input") is not None or tool.get("name")]
        summary["tool_results"] = tool_results
        if include_text and text_chunks:
            summary["text"] = redact_text("\n".join(text_chunks))
        elif text_chunks:
            summary["text_hash"] = short_hash("\n".join(text_chunks))
    return summary


def value_summary(value: Any) -> dict[str, Any]:
    """Summarize a value without retaining raw user/tool text."""
    summary: dict[str, Any] = {"value_type": type(value).__name__}
    if isinstance(value, str):
        summary.update({"text_len": len(value), "text_hash": short_hash(value)})
        return summary
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
        summary.update({"text_len": len(text), "text_hash": short_hash(text)})
        return summary
    if isinstance(value, list):
        summary["items"] = len(value)
    elif isinstance(value, dict):
        summary["keys"] = sorted(str(key) for key in value.keys())[:100]
    try:
        payload = json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        payload = str(value)
    summary.update({"json_len": len(payload), "json_hash": short_hash(payload)})
    return summary


class AgentIngestor:
    def __init__(self, config: RuntimeConfig, sink: SentrySink, memory: MemoryStore | None = None) -> None:
        self.config = config
        self.sink = sink
        self.memory = memory
        self.git = GitMetadataCache()

    def initialize_state(self, state: dict[str, Any], backfill_since: datetime | None = None) -> None:
        cutoff_seconds = int(backfill_since.timestamp()) if backfill_since else None
        cutoff_ms = int(backfill_since.timestamp() * 1000) if backfill_since else None
        if self.config.codex_logs_db.exists():
            try:
                conn = sqlite_connect(self.config.codex_logs_db)
                if conn:
                    if cutoff_seconds:
                        row = conn.execute(
                            "select coalesce(max(id), 0) as max_id from logs where target='codex_otel.log_only' and ts < ?",
                            (cutoff_seconds,),
                        ).fetchone()
                    else:
                        row = conn.execute("select coalesce(max(id), 0) as max_id from logs where target='codex_otel.log_only'").fetchone()
                    state["codex_logs_last_id"] = int(row["max_id"] or 0)
                    conn.close()
            except Exception as exc:
                log(f"codex log initialization failed: {exc}")

        if self.config.codex_state_db.exists():
            try:
                conn = sqlite_connect(self.config.codex_state_db)
                if conn:
                    if cutoff_ms:
                        state["codex_threads_last_updated_ms"] = cutoff_ms
                    else:
                        row = conn.execute(
                            "select updated_at_ms, id from threads order by updated_at_ms desc, id desc limit 1"
                        ).fetchone()
                        state["codex_threads_last_updated_ms"] = int(row["updated_at_ms"] or 0) if row else 0
                        state["codex_threads_last_id"] = str(row["id"] or "") if row else ""
                    conn.close()
            except Exception as exc:
                log(f"codex thread initialization failed: {exc}")

        self._initialize_file_offsets(state.setdefault("claude_files", {}), self.config.claude_projects_glob, cutoff_seconds)
        self._initialize_file_offsets(state.setdefault("pi_files", {}), self.config.pi_sessions_glob, cutoff_seconds)
        state["initialized_at"] = int(time.time())
        state["initialization_mode"] = f"backfill-since:{backfill_since.isoformat()}" if backfill_since else "current-watermarks"

    def process_once(self, state: dict[str, Any], max_batch: int, since: datetime | None = None) -> dict[str, int]:
        return {
            "codex_logs": self.process_codex_logs(state, max_batch),
            "codex_threads": self.process_codex_threads(state, max_batch),
            "claude_records": self.process_claude_files(state, max_batch, since=since),
            "pi_records": self.process_pi_files(state, max_batch, since=since),
        }

    def _initialize_file_offsets(self, files_state: dict[str, Any], pattern: str, cutoff_seconds: int | None) -> None:
        for path_text in glob.iglob(pattern, recursive=True):
            path = Path(path_text)
            if "subagent-artifacts" in path.parts:
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            offset = 0 if cutoff_seconds and stat.st_mtime >= cutoff_seconds else stat.st_size
            files_state[str(path)] = {"offset": offset, "inode": stat.st_ino, "mtime": stat.st_mtime}

    def emit(self, trace: NormalizedTrace) -> None:
        self.sink.capture(trace)
        if self.memory and self.config.record_memory and not self.sink.dry_run:
            try:
                self.memory.record_trace(trace)
            except Exception as exc:
                log(f"memory record skipped for {trace.title}: {exc}")

    def process_codex_logs(self, state: dict[str, Any], max_batch: int) -> int:
        conn = sqlite_connect(self.config.codex_logs_db)
        if not conn:
            return 0
        last_id = int(state.get("codex_logs_last_id", 0) or 0)
        rows = conn.execute(
            """
            select id, ts, ts_nanos, level, target, feedback_log_body, module_path, file, line, thread_id, process_uuid, estimated_bytes
            from logs
            where id > ? and target = 'codex_otel.log_only'
            order by id asc
            limit ?
            """,
            (last_id, max_batch),
        ).fetchall()
        conn.close()

        count = 0
        for row in rows:
            trace = codex_log_to_trace(row, self.git, self.config.include_text)
            self.emit(trace)
            state["codex_logs_last_id"] = int(row["id"])
            count += 1
        return count

    def process_codex_threads(self, state: dict[str, Any], max_batch: int) -> int:
        conn = sqlite_connect(self.config.codex_state_db)
        if not conn:
            return 0
        last_updated = int(state.get("codex_threads_last_updated_ms", 0) or 0)
        last_id = str(state.get("codex_threads_last_id") or "")
        if last_updated and "codex_threads_last_id" not in state:
            last_id = "\uffff"
        rows = conn.execute(
            """
            select id, updated_at_ms, created_at_ms, source, model_provider, cwd, title, tokens_used,
                   first_user_message, model, reasoning_effort, cli_version, git_branch, git_sha, git_origin_url
            from threads
            where updated_at_ms > ? or (updated_at_ms = ? and id > ?)
            order by updated_at_ms asc, id asc
            limit ?
            """,
            (last_updated, last_updated, last_id, max_batch),
        ).fetchall()
        conn.close()

        count = 0
        for row in rows:
            trace = codex_thread_to_trace(row, self.git, self.config.include_text)
            self.emit(trace)
            state["codex_threads_last_updated_ms"] = int(row["updated_at_ms"] or 0)
            state["codex_threads_last_id"] = str(row["id"] or "")
            count += 1
        return count

    def process_claude_files(self, state: dict[str, Any], max_batch: int, since: datetime | None = None) -> int:
        files_state: dict[str, Any] = state.setdefault("claude_files", {})
        processed = 0
        for path_text in glob.iglob(self.config.claude_projects_glob, recursive=True):
            if processed >= max_batch:
                break
            path = Path(path_text)
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            file_key = str(path)
            entry = files_state.get(file_key)
            if entry is None:
                entry = {"offset": 0, "inode": stat.st_ino, "mtime": stat.st_mtime}
                files_state[file_key] = entry
            offset = int(entry.get("offset", 0) or 0)
            if offset > stat.st_size:
                offset = 0
            if offset == stat.st_size:
                entry.update({"offset": offset, "inode": stat.st_ino, "mtime": stat.st_mtime})
                continue

            with path.open("rb") as handle:
                handle.seek(offset)
                for raw_line in handle:
                    if processed >= max_batch:
                        break
                    offset += len(raw_line)
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    record_ts = parse_timestamp(record.get("timestamp"))
                    if since and record_ts and record_ts < since:
                        continue
                    for trace in claude_record_to_traces(record, self.git, self.config.include_text):
                        trace.extra["jsonl_path"] = file_key
                        self.emit(trace)
                    processed += 1
            entry.update({"offset": offset, "inode": stat.st_ino, "mtime": stat.st_mtime})
        return processed

    def process_pi_files(self, state: dict[str, Any], max_batch: int, since: datetime | None = None) -> int:
        files_state: dict[str, Any] = state.setdefault("pi_files", {})
        processed = 0
        for path_text in glob.iglob(self.config.pi_sessions_glob, recursive=True):
            if processed >= max_batch:
                break
            path = Path(path_text)
            if "subagent-artifacts" in path.parts:
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            file_key = str(path)
            entry = files_state.get(file_key)
            if entry is None:
                entry = {"offset": 0, "inode": stat.st_ino, "mtime": stat.st_mtime}
                files_state[file_key] = entry
            offset = int(entry.get("offset", 0) or 0)
            if offset > stat.st_size:
                offset = 0
            if offset == stat.st_size:
                entry.update({"offset": offset, "inode": stat.st_ino, "mtime": stat.st_mtime})
                continue

            header = read_pi_session_header(path)
            with path.open("rb") as handle:
                handle.seek(offset)
                for raw_line in handle:
                    if processed >= max_batch:
                        break
                    offset += len(raw_line)
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    record_ts = parse_timestamp(record.get("timestamp"))
                    if since and record_ts and record_ts < since:
                        continue
                    if record.get("type") == "session":
                        header = record
                    for trace in pi_record_to_traces(record, header, self.git, self.config.include_text):
                        trace.extra["jsonl_path"] = file_key
                        self.emit(trace)
                    processed += 1
            entry.update({"offset": offset, "inode": stat.st_ino, "mtime": stat.st_mtime})
        return processed


def read_pi_session_header(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            line = handle.readline().decode("utf-8", errors="replace").strip()
    except OSError:
        return {}
    if not line:
        return {}
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return record if isinstance(record, dict) and record.get("type") == "session" else {}


def flatten_pi_usage(usage: dict[str, Any] | None) -> tuple[dict[str, int | float], dict[str, int | float]]:
    if not isinstance(usage, dict):
        return {}, {}
    token_usage: dict[str, int | float] = {}
    measurements: dict[str, int | float] = {}
    for source_key, target_key in (
        ("input", "input_tokens"),
        ("output", "output_tokens"),
        ("cacheRead", "cache_read_input_tokens"),
        ("cacheWrite", "cache_creation_input_tokens"),
    ):
        value = usage.get(source_key)
        if isinstance(value, (int, float)):
            token_usage[target_key] = value
    total_tokens = usage.get("totalTokens")
    if isinstance(total_tokens, (int, float)):
        measurements["total_tokens"] = total_tokens
    cost = usage.get("cost")
    if isinstance(cost, dict):
        for source_key, target_key in (
            ("input", "input_cost_usd"),
            ("output", "output_cost_usd"),
            ("cacheRead", "cache_read_cost_usd"),
            ("cacheWrite", "cache_write_cost_usd"),
            ("total", "cost_usd"),
        ):
            value = cost.get(source_key)
            if isinstance(value, (int, float)):
                measurements[target_key] = value
    return token_usage, measurements


def pi_record_to_traces(record: dict[str, Any], header: dict[str, Any], git: GitMetadataCache, include_text: bool) -> list[NormalizedTrace]:
    record_type = str(record.get("type", "unknown")).replace("-", "_")
    session_id = str(header.get("id") or record.get("sessionId") or record.get("id") or "")
    cwd = record.get("cwd") or header.get("cwd")
    timestamp = parse_timestamp(record.get("timestamp")) or parse_timestamp(header.get("timestamp"))
    git_meta = git.for_cwd(cwd)
    base_extra = {
        "entry_id": record.get("id"),
        "parent_id": record.get("parentId"),
        "session_version": header.get("version"),
        "parent_session": header.get("parentSession"),
    }
    base_tags = {"entry_type": record_type}
    base = {
        "agent": "pi",
        "timestamp": timestamp,
        "session_id": session_id or None,
        "trace_id": session_id or None,
        "project": infer_project(cwd),
        "cwd": cwd,
        "repo": git_meta["repo"],
        "git_branch": git_meta["git_branch"],
        "git_sha": git_meta["git_sha"],
    }
    traces: list[NormalizedTrace] = []

    def append_trace(trace: NormalizedTrace) -> None:
        if "cost_usd" not in trace.measurements:
            apply_cost_estimate(trace)
        traces.append(trace)

    if record_type == "session":
        append_trace(
            NormalizedTrace(
                **base,
                kind="session_start",
                source_event_id=f"pi:{session_id}:session",
                tags=base_tags,
                extra={**base_extra, "session": value_summary(record)},
            )
        )
        return traces

    if record_type == "model_change":
        append_trace(
            NormalizedTrace(
                **base,
                kind="model_change",
                source_event_id=f"pi:{session_id}:{record.get('id')}:model-change",
                model=record.get("modelId"),
                provider=record.get("provider"),
                tags=base_tags,
                extra=base_extra,
            )
        )
        return traces

    if record_type == "thinking_level_change":
        append_trace(
            NormalizedTrace(
                **base,
                kind="thinking_level_change",
                source_event_id=f"pi:{session_id}:{record.get('id')}:thinking-level-change",
                tags={**base_tags, "thinking_level": record.get("thinkingLevel")},
                extra=base_extra,
            )
        )
        return traces

    message = record.get("message")
    if isinstance(message, dict):
        traces.extend(pi_message_to_traces(record, message, base, base_extra, base_tags, include_text))
        return traces

    append_trace(
        NormalizedTrace(
            **base,
            kind=record_type,
            source_event_id=f"pi:{session_id}:{record.get('id')}:{record_type}",
            tags=base_tags,
            extra={**base_extra, "record": scrub(record) if include_text else value_summary(record)},
        )
    )
    return traces


def pi_message_to_traces(
    record: dict[str, Any],
    message: dict[str, Any],
    base: dict[str, Any],
    base_extra: dict[str, Any],
    base_tags: dict[str, Any],
    include_text: bool,
) -> list[NormalizedTrace]:
    role = str(message.get("role", "message")).replace("-", "_")
    entry_id = record.get("id")
    session_id = base.get("session_id") or ""
    timestamp = parse_timestamp(message.get("timestamp")) or base.get("timestamp")
    content = message.get("content")
    summary = content_summary(content, include_text)
    token_usage, measurements = flatten_pi_usage(message.get("usage"))
    stop_reason = message.get("stopReason")
    success = None
    level = "info"
    if role == "assistant" and stop_reason in {"error", "aborted"}:
        success = False
        level = "error"
    if role == "toolResult":
        success = not bool(message.get("isError"))
        level = "error" if message.get("isError") else "info"
    if role == "bashExecution":
        exit_code = message.get("exitCode")
        cancelled = bool(message.get("cancelled"))
        success = (exit_code == 0) and not cancelled if exit_code is not None else not cancelled
        level = "error" if not success else "info"

    kind_by_role = {
        "user": "user_prompt",
        "assistant": "assistant_turn",
        "toolResult": "tool_result",
        "bashExecution": "bash_execution",
        "custom": "custom_message",
        "branchSummary": "branch_summary",
        "compactionSummary": "compaction_summary",
    }
    kind = kind_by_role.get(role, role)
    tool_name = message.get("toolName") if role == "toolResult" else ("bash" if role == "bashExecution" else None)
    extra = {
        **base_extra,
        "role": role,
        "content_summary": summary,
        "stop_reason": stop_reason,
        "api": message.get("api"),
    }
    if message.get("errorMessage"):
        extra["error_message"] = redact_text(str(message.get("errorMessage")))
    if role == "toolResult" and message.get("details") is not None:
        extra["details"] = scrub(message.get("details")) if include_text else value_summary(message.get("details"))
    if role == "bashExecution":
        extra.update(
            {
                "command": redact_text(str(message.get("command") or "")) if include_text else value_summary(message.get("command")),
                "output": redact_text(str(message.get("output") or "")) if include_text else value_summary(message.get("output")),
                "cancelled": message.get("cancelled"),
                "truncated": message.get("truncated"),
                "full_output_path": message.get("fullOutputPath"),
            }
        )

    trace = NormalizedTrace(
        **{**base, "timestamp": timestamp},
        kind=kind,
        level=level,
        source_event_id=f"pi:{session_id}:{entry_id}:{kind}",
        turn_id=str(entry_id) if entry_id else None,
        model=message.get("model"),
        provider=message.get("provider"),
        tool_name=tool_name,
        tool_kind="pi_tool" if tool_name else None,
        command_kind="shell" if role == "bashExecution" else None,
        success=success,
        exit_code=message.get("exitCode") if role == "bashExecution" and isinstance(message.get("exitCode"), int) else None,
        token_usage=token_usage,
        measurements=measurements,
        tags={**base_tags, "role": role, "stop_reason": stop_reason, "tool_name": tool_name},
        extra=extra,
    )
    traces = [trace]
    for tool in summary.get("tool_uses", []):
        traces.append(
            NormalizedTrace(
                **{**base, "timestamp": timestamp},
                kind="tool_use",
                source_event_id=f"pi:{session_id}:{entry_id}:tool:{tool.get('id') or tool.get('name')}",
                turn_id=str(entry_id) if entry_id else None,
                model=message.get("model"),
                provider=message.get("provider"),
                tool_name=tool.get("name"),
                tool_kind="pi_tool",
                tags={**base_tags, "role": role, "tool_id": tool.get("id")},
                extra={**base_extra, "tool": tool},
            )
        )
    if role == "toolResult" and message.get("toolName") == "subagent":
        traces.extend(pi_subagent_result_traces(record, message, base, base_extra, base_tags, include_text))
    for item in traces:
        if "cost_usd" not in item.measurements:
            apply_cost_estimate(item)
    return traces


def pi_subagent_result_traces(
    record: dict[str, Any],
    message: dict[str, Any],
    base: dict[str, Any],
    base_extra: dict[str, Any],
    base_tags: dict[str, Any],
    include_text: bool,
) -> list[NormalizedTrace]:
    details = message.get("details")
    if not isinstance(details, dict) or not isinstance(details.get("results"), list):
        return []
    traces: list[NormalizedTrace] = []
    session_id = base.get("session_id") or ""
    entry_id = record.get("id")
    timestamp = parse_timestamp(message.get("timestamp")) or base.get("timestamp")
    for idx, result in enumerate(details.get("results") or []):
        if not isinstance(result, dict):
            continue
        progress = result.get("progressSummary") if isinstance(result.get("progressSummary"), dict) else {}
        duration_ms = progress.get("durationMs") if isinstance(progress, dict) else None
        task = str(result.get("task") or "")
        exit_code = result.get("exitCode")
        subagent = result.get("agent") or "subagent"
        extra = {
            **base_extra,
            "parent_tool_call_id": message.get("toolCallId"),
            "subagent": subagent,
            "mode": details.get("mode"),
            "task": redact_text(task) if include_text else value_summary(task),
            "skills": result.get("skills") if isinstance(result.get("skills"), list) else [],
            "session_file": result.get("sessionFile"),
            "artifact_paths": result.get("artifactPaths") if isinstance(result.get("artifactPaths"), dict) else {},
            "progress_summary": scrub(progress) if include_text else value_summary(progress),
        }
        traces.append(
            NormalizedTrace(
                **{**base, "timestamp": timestamp},
                kind="subagent_run",
                source_event_id=f"pi:{session_id}:{entry_id}:subagent:{idx}:{subagent}",
                turn_id=str(entry_id) if entry_id else None,
                model=result.get("model"),
                tool_name=str(subagent),
                tool_kind="pi_subagent",
                duration_ms=float(duration_ms) if isinstance(duration_ms, (int, float)) else None,
                success=(exit_code == 0) if isinstance(exit_code, int) else None,
                exit_code=exit_code if isinstance(exit_code, int) else None,
                tags={**base_tags, "role": "toolResult", "subagent": subagent, "mode": details.get("mode")},
                extra=extra,
            )
        )
    return traces


def codex_log_to_trace(row: sqlite3.Row, git: GitMetadataCache, include_text: bool = False) -> NormalizedTrace:
    body = row["feedback_log_body"] or ""
    parsed = parse_codex_kv(body)
    kind = parsed.get("event.name") or parsed.get("otel.name") or "otel_log"
    timestamp = parse_timestamp(parsed.get("event.timestamp")) or parse_timestamp(row["ts"])
    cwd = parsed.get("cwd")
    git_meta = git.for_cwd(cwd)
    measurements: dict[str, int | float] = {}
    if parsed.get("duration_ms"):
        try:
            measurements["duration_ms"] = float(parsed["duration_ms"])
        except ValueError:
            pass
    if row["estimated_bytes"]:
        measurements["estimated_bytes"] = int(row["estimated_bytes"])
    token_usage: dict[str, int | float] = {}
    raw_input_tokens = _int_or_none(parsed.get("input_token_count"))
    cached_input_tokens = _int_or_none(parsed.get("cached_token_count")) or 0
    if raw_input_tokens is not None:
        token_usage["input_tokens_total"] = raw_input_tokens
        token_usage["input_tokens"] = max(raw_input_tokens - cached_input_tokens, 0)
    if cached_input_tokens:
        token_usage["cache_read_input_tokens"] = cached_input_tokens
    for source_key, target_key in (
        ("output_token_count", "output_tokens"),
        ("reasoning_token_count", "reasoning_tokens"),
        ("tool_token_count", "tool_tokens"),
    ):
        value = _int_or_none(parsed.get(source_key))
        if value is not None:
            token_usage[target_key] = value
    success = _bool(parsed.get("success"))
    session_id = row["thread_id"] or parsed.get("thread.id") or parsed.get("conversation.id")
    turn_id = parsed.get("turn.id") or parsed.get("submission.id")
    trace = NormalizedTrace(
        agent="codex",
        kind=f"codex.{kind}" if not kind.startswith("codex.") else kind,
        timestamp=timestamp,
        level=row["level"],
        source_event_id=f"codex-log:{row['id']}",
        session_id=session_id,
        turn_id=turn_id,
        trace_id=parsed.get("trace_id") or session_id,
        project=infer_project(cwd),
        cwd=cwd,
        repo=git_meta["repo"],
        git_branch=git_meta["git_branch"],
        git_sha=git_meta["git_sha"],
        model=parsed.get("model") or parsed.get("slug"),
        provider=parsed.get("provider"),
        agent_version=parsed.get("app.version"),
        duration_ms=measurements.pop("duration_ms", None),
        success=success,
        token_usage=token_usage,
        measurements=measurements,
        tags={
            "level": row["level"],
            "target": row["target"],
            "originator": parsed.get("originator"),
            "transport": parsed.get("transport"),
            "wire_api": parsed.get("wire_api"),
            "api_path": parsed.get("api.path"),
            "app_version": parsed.get("app.version"),
            "auth_mode": parsed.get("auth_mode"),
            "event_kind": parsed.get("event.kind"),
        },
        extra={
            "log_id": row["id"],
            "process_uuid": row["process_uuid"],
            "module_path": row["module_path"],
            "file": row["file"],
            "line": row["line"],
            "codex_otel": scrub(parsed) if include_text else value_summary(parsed),
            "body": redact_text(body) if include_text else value_summary(body),
        },
    )
    apply_cost_estimate(trace)
    return trace


def codex_thread_to_trace(row: sqlite3.Row, git: GitMetadataCache, include_text: bool) -> NormalizedTrace:
    cwd = row["cwd"]
    first_user_message = row["first_user_message"] or ""
    title = row["title"] or ""
    git_meta = git.for_cwd(cwd)
    extra: dict[str, Any] = {
        "thread_id": row["id"],
        "title_len": len(title),
        "title_hash": short_hash(title),
        "first_user_message_len": len(first_user_message),
        "first_user_message_hash": short_hash(first_user_message),
        "created_at_ms": row["created_at_ms"],
        "updated_at_ms": row["updated_at_ms"],
        "git_origin_url": row["git_origin_url"],
    }
    if include_text:
        extra["title"] = redact_text(title)
        extra["first_user_message"] = redact_text(first_user_message)
    trace = NormalizedTrace(
        agent="codex",
        kind="thread_update",
        timestamp=parse_timestamp((row["updated_at_ms"] or 0) / 1000),
        source_event_id=f"codex-thread:{row['id']}:{row['updated_at_ms']}",
        session_id=row["id"],
        project=infer_project(cwd),
        cwd=cwd,
        repo=git_meta["repo"],
        git_branch=row["git_branch"] or git_meta["git_branch"],
        git_sha=row["git_sha"] or git_meta["git_sha"],
        model=row["model"],
        provider=row["model_provider"],
        agent_version=row["cli_version"],
        measurements={"tokens_used": int(row["tokens_used"] or 0)},
        tags={"source": row["source"], "reasoning_effort": row["reasoning_effort"]},
        extra=extra,
    )
    apply_cost_estimate(trace)
    return trace


def claude_record_to_traces(record: dict[str, Any], git: GitMetadataCache, include_text: bool) -> list[NormalizedTrace]:
    record_type = str(record.get("type", "unknown")).replace("-", "_")
    message = record.get("message")
    timestamp = parse_timestamp(record.get("timestamp"))
    cwd = record.get("cwd")
    git_meta = git.for_cwd(cwd)
    session_id = record.get("sessionId")
    uuid = record.get("uuid")
    base_extra = {
        "uuid": uuid,
        "parent_uuid": record.get("parentUuid"),
        "request_id": record.get("requestId"),
        "timestamp": to_timestamp(timestamp),
        "source_tool_assistant_uuid": record.get("sourceToolAssistantUUID"),
        "origin": record.get("origin"),
        "permission_mode": record.get("permissionMode"),
    }
    base_tags = {
        "entrypoint": record.get("entrypoint"),
        "is_sidechain": record.get("isSidechain"),
        "agent_id": record.get("agentId"),
        "record_type": record_type,
    }
    base = {
        "agent": "claude-code",
        "timestamp": timestamp,
        "session_id": session_id,
        "turn_id": uuid,
        "project": infer_project(cwd),
        "cwd": cwd,
        "repo": git_meta["repo"],
        "git_branch": record.get("gitBranch") or git_meta["git_branch"],
        "git_sha": git_meta["git_sha"],
        "agent_version": record.get("version"),
    }
    traces: list[NormalizedTrace] = []
    def append_trace(trace: NormalizedTrace) -> None:
        apply_cost_estimate(trace)
        traces.append(trace)

    if isinstance(message, dict):
        content = message.get("content")
        summary = content_summary(content, include_text)
        usage = message.get("usage")
        token_usage = flatten_usage(usage)
        model = message.get("model")
        role = message.get("role")
        stop_reason = message.get("stop_reason")
        tags = {**base_tags, "role": role, "stop_reason": stop_reason, "request_id": record.get("requestId")}
        extra = {**base_extra, "message_id": message.get("id"), "content_summary": summary, "usage": scrub(usage) if include_text else usage}
        if record_type == "assistant":
            append_trace(
                NormalizedTrace(
                    **base,
                    kind="assistant_turn",
                    source_event_id=f"claude:{uuid}:assistant",
                    model=model,
                    tags=tags,
                    extra=extra,
                    token_usage=token_usage,
                )
            )
            for tool in summary.get("tool_uses", []):
                append_trace(
                    NormalizedTrace(
                        **base,
                        kind="tool_use",
                        source_event_id=f"claude:{uuid}:tool:{tool.get('id') or tool.get('name')}",
                        model=model,
                        tool_name=tool.get("name"),
                        tool_kind="claude_tool",
                        tags={**tags, "tool_id": tool.get("id")},
                        extra={**base_extra, "tool": tool},
                    )
                )
        elif record_type == "user" and record.get("toolUseResult"):
            tool_use_result = record.get("toolUseResult")
            append_trace(
                NormalizedTrace(
                    **base,
                    kind="tool_result",
                    level="error" if _tool_result_is_error(content) else "info",
                    source_event_id=f"claude:{uuid}:tool-result",
                    model=model,
                    tags=tags,
                    extra={
                        **extra,
                        "tool_use_result": scrub(tool_use_result) if include_text else value_summary(tool_use_result),
                    },
                    token_usage=token_usage,
                )
            )
        elif record_type == "user":
            append_trace(
                NormalizedTrace(
                    **base,
                    kind="user_prompt",
                    source_event_id=f"claude:{uuid}:user",
                    model=model,
                    tags=tags,
                    extra=extra,
                    token_usage=token_usage,
                )
            )
        else:
            append_trace(
                NormalizedTrace(
                    **base,
                    kind=record_type,
                    source_event_id=f"claude:{uuid}:{record_type}",
                    model=model,
                    tags=tags,
                    extra=extra,
                    token_usage=token_usage,
                )
            )
    else:
        append_trace(
            NormalizedTrace(
                **base,
                kind=record_type,
                source_event_id=f"claude:{uuid}:{record_type}",
                extra=base_extra,
            )
        )
    return traces


def _tool_result_is_error(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error") for block in content)


def _bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).lower()
    if text in {"1", "true", "yes", "ok", "success"}:
        return True
    if text in {"0", "false", "no", "error", "failed"}:
        return False
    return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def run_bridge_loop(
    config: RuntimeConfig,
    sink: SentrySink,
    memory: MemoryStore | None,
    state: dict[str, Any],
    save_state: Any,
    *,
    loop: bool,
    once: bool,
    backfill_minutes: int | None = None,
) -> int:
    ingestor = AgentIngestor(config, sink, memory)
    backfill_since = None
    if backfill_minutes is not None:
        if backfill_minutes <= 0:
            log("--minutes must be positive")
            return 64
        backfill_since = datetime.fromtimestamp(time.time() - backfill_minutes * 60, tz=timezone.utc)
        state = empty_state()

    running = True
    local_only_logged = False
    while running:
        if not sink.configure():
            log("Sentry sink could not be configured; waiting.")
            if once:
                return 2
            time.sleep(max(config.poll_seconds, 30))
            continue
        if not config.sentry_dsn and not sink.dry_run and not local_only_logged:
            log("SENTRY_DSN is not configured; running local-only memory capture.")
            local_only_logged = True
        if not state.get("initialized_at"):
            ingestor.initialize_state(state, backfill_since=backfill_since)
            save_state(state)
            log(f"initialized for backfill since {backfill_since.isoformat()}" if backfill_since else "initialized at current Claude/Codex watermarks")
        try:
            batch_count = 0
            while True:
                counts = ingestor.process_once(state, max_batch=config.max_batch, since=backfill_since)
                save_state(state)
                total = sum(counts.values())
                if total:
                    log(f"exported usage batch: {counts}")
                batch_count += 1
                if not backfill_since or total == 0:
                    break
                if batch_count >= env_int("AGENT_VM_BACKFILL_MAX_BATCHES", env_int("AGENT_SENTRY_BACKFILL_MAX_BATCHES", 200)):
                    log("backfill stopped at max batch limit")
                    break
        except Exception as exc:
            log(f"bridge batch failed: {exc}")
            traceback.print_exc()
            sink.capture_exception(exc)
        if once or not loop:
            break
        time.sleep(config.poll_seconds)
    sink.flush(timeout=env_int("AGENT_VM_FLUSH_TIMEOUT", env_int("AGENT_SENTRY_FLUSH_TIMEOUT", 30)))
    return 0
