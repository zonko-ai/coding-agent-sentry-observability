from __future__ import annotations

import json
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import RuntimeConfig
from .memory import MemoryStore
from .model import NormalizedTrace
from .sentry_sink import SentrySink, USAGE_SCHEMA
from .timeutil import parse_timestamp, utc_now


def capture_sentry_session_rows(
    sink: SentrySink,
    memory_store: MemoryStore,
    minutes: int,
    *,
    state: dict[str, Any] | None = None,
) -> int:
    rows = _session_rows(memory_store, minutes)
    exported = state.setdefault("sentry_session_rows", {}) if state is not None else None
    sent = 0
    min_sentry_timestamp = utc_now() - timedelta(days=4)
    clamped = 0
    for row in rows:
        identity = f"{USAGE_SCHEMA}:{row['agent']}:{row['session_id']}"
        last_seen_epoch = int(row.get("last_seen_epoch") or row.get("started_epoch") or 0)
        if exported is not None and int(exported.get(identity, 0) or 0) >= last_seen_epoch:
            continue
        trace = _session_trace(row)
        if trace.timestamp is not None and trace.timestamp < min_sentry_timestamp:
            trace.extra.setdefault("session", {})["original_timestamp"] = trace.timestamp.isoformat()
            trace.tags["session_timestamp_clamped"] = "true"
            trace.timestamp = min_sentry_timestamp + timedelta(seconds=clamped)
            clamped += 1
        sink.capture(trace)
        if exported is not None:
            exported[identity] = last_seen_epoch
        sent += 1
    return sent


def export_sentry_session_rollups(
    config: RuntimeConfig,
    memory_store: MemoryStore,
    minutes: int,
    *,
    dry_run: bool = False,
    state: dict[str, Any] | None = None,
) -> int:
    sink = SentrySink(config, dry_run=dry_run)
    if not sink.configure():
        return 0
    sent = capture_sentry_session_rows(sink, memory_store, minutes, state=state)
    sink.flush(timeout=120)
    return sent


def _session_rows(memory_store: MemoryStore, minutes: int) -> list[dict[str, Any]]:
    cutoff_epoch = max(int(time.time()) - minutes * 60, 0)
    memory_store.initialize()
    with closing(memory_store.connect()) as conn:
        records = conn.execute(
            """
            select s.external_session_id, s.project, s.cwd, s.repo, s.git_branch,
                   s.started_at, s.started_at_epoch, s.last_seen_at, s.last_seen_at_epoch,
                   s.status, s.metadata_json, a.name as agent,
                   (
                     select e.model from events e
                     where e.session_db_id = s.id and e.model is not null and e.model != ''
                     order by e.timestamp_epoch desc, e.id desc
                     limit 1
                   ) as latest_model,
                   (
                     select count(*) from events e where e.session_db_id = s.id
                   ) as event_count,
                   (
                     select count(*) from tool_calls t where t.session_db_id = s.id
                   ) as tool_call_count
            from agent_sessions s
            join agents a on a.id = s.agent_id
            where case
                    when coalesce(s.last_seen_at_epoch, s.started_at_epoch, 0) > 9999999999
                    then coalesce(s.last_seen_at_epoch, s.started_at_epoch, 0) / 1000
                    else coalesce(s.last_seen_at_epoch, s.started_at_epoch, 0)
                  end >= ?
            order by case
                       when coalesce(s.last_seen_at_epoch, s.started_at_epoch, 0) > 9999999999
                       then coalesce(s.last_seen_at_epoch, s.started_at_epoch, 0) / 1000
                       else coalesce(s.last_seen_at_epoch, s.started_at_epoch, 0)
                     end asc
            """,
            (cutoff_epoch,),
        ).fetchall()
    rows: list[dict[str, Any]] = []
    for record in records:
        metadata = _json_object(record["metadata_json"])
        started_epoch = _normalize_epoch(record["started_at_epoch"])
        last_seen_epoch = _normalize_epoch(record["last_seen_at_epoch"]) or started_epoch
        rows.append(
            {
                "agent": record["agent"],
                "session_id": str(record["external_session_id"]),
                "project": record["project"] or "unknown",
                "cwd": record["cwd"],
                "repo": record["repo"],
                "git_branch": record["git_branch"],
                "started_at": record["started_at"],
                "started_epoch": started_epoch,
                "last_seen_at": record["last_seen_at"],
                "last_seen_epoch": last_seen_epoch,
                "status": record["status"] or "active",
                "model": record["latest_model"] or metadata.get("model"),
                "provider": metadata.get("provider"),
                "agent_version": metadata.get("agent_version"),
                "event_count": int(record["event_count"] or 0),
                "tool_call_count": int(record["tool_call_count"] or 0),
            }
        )
    return rows


def _session_trace(row: dict[str, Any]) -> NormalizedTrace:
    last_seen = _timestamp(row.get("last_seen_at"), row.get("last_seen_epoch")) or utc_now()
    started = _timestamp(row.get("started_at"), row.get("started_epoch"))
    session_age_ms = None
    if started:
        session_age_ms = max((last_seen - started).total_seconds() * 1000, 1.0)
    agent = str(row.get("agent") or "unknown")
    session_id = str(row.get("session_id") or "unknown")
    last_seen_epoch = int(row.get("last_seen_epoch") or 0)
    return NormalizedTrace(
        agent=agent,
        kind=f"session_v{USAGE_SCHEMA.rsplit('_v', 1)[-1]}",
        timestamp=last_seen,
        level="info",
        source_event_id=f"{USAGE_SCHEMA}:session:{agent}:{session_id}:{last_seen_epoch}",
        session_id=session_id,
        project=str(row.get("project") or "unknown"),
        cwd=row.get("cwd") if isinstance(row.get("cwd"), str) else None,
        repo=row.get("repo") if isinstance(row.get("repo"), str) else None,
        git_branch=row.get("git_branch") if isinstance(row.get("git_branch"), str) else None,
        model=row.get("model") if isinstance(row.get("model"), str) else None,
        provider=row.get("provider") if isinstance(row.get("provider"), str) else None,
        agent_version=row.get("agent_version") if isinstance(row.get("agent_version"), str) else None,
        duration_ms=1.0,
        success=True,
        measurements={
            "session_events": int(row.get("event_count") or 0),
            "session_tool_calls": int(row.get("tool_call_count") or 0),
            **({"session_age_ms": session_age_ms} if session_age_ms is not None else {}),
        },
        tags={"usage_rollup": "session", "session_status": str(row.get("status") or "active")},
        extra={
            "session": {
                "started_at": row.get("started_at"),
                "last_seen_at": row.get("last_seen_at"),
                "event_count": int(row.get("event_count") or 0),
                "tool_call_count": int(row.get("tool_call_count") or 0),
            }
        },
    )


def _timestamp(value: Any, epoch: Any) -> datetime | None:
    parsed = parse_timestamp(value)
    if parsed:
        return parsed
    try:
        normalized = _normalize_epoch(epoch)
        if normalized:
            return datetime.fromtimestamp(normalized, timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return None


def _normalize_epoch(value: Any) -> int:
    try:
        epoch = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if epoch > 9999999999:
        return epoch // 1000
    return epoch


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}
