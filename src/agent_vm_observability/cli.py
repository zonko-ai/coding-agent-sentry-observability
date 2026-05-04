from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import VERSION
from .config import get_config, load_env_files
from .ingest import AgentIngestor, log, run_bridge_loop
from .launchd import install_launchd, launchd_status, start_launchd, stop_launchd
from .local_dashboard import run_dashboard
from .memory import MemoryStore, USAGE_SUM_KEYS, _json_object, _usage_identity, _usage_measurements
from .model import NormalizedTrace
from .redaction import short_hash
from .sentry_dashboards import SentryDashboardClient
from .sentry_sink import SentrySink, USAGE_SCHEMA
from .state import StateStore, empty_state
from .timeutil import parse_timestamp, utc_now


def main(argv: list[str] | None = None) -> int:
    load_env_files()
    parser = argparse.ArgumentParser(description="Local observability and memory for coding agents.")
    sub = parser.add_subparsers(dest="command")

    bridge = sub.add_parser("bridge", help="Run the Sentry/memory bridge.")
    bridge.add_argument("--loop", action="store_true")
    bridge.add_argument("--once", action="store_true")
    bridge.add_argument("--dry-run", action="store_true")
    bridge.add_argument("--reset-state", action="store_true")
    bridge.add_argument("--backfill-minutes", type=int)

    sub.add_parser("status", help="Show bridge, Sentry, memory, and launchd status.")

    self_test = sub.add_parser("self-test", help="Send deterministic test traces.")
    self_test.add_argument("--dry-run", action="store_true")

    backfill = sub.add_parser("backfill", help="Export recent history without moving live state by default.")
    backfill.add_argument("--minutes", type=int, required=True)
    backfill.add_argument("--dry-run", action="store_true")
    backfill.add_argument("--update-state", action="store_true", help="Use the configured state file instead of an isolated temporary state.")

    install = sub.add_parser("install-launchd", help="Install the packaged LaunchAgent.")
    install.add_argument("--no-load", action="store_true")
    sub.add_parser("start-launchd", help="Start the packaged LaunchAgent.")
    sub.add_parser("stop-launchd", help="Stop the packaged LaunchAgent.")

    sentry = sub.add_parser("sentry", help="Sentry helpers.")
    sentry_sub = sentry.add_subparsers(dest="sentry_command")
    apply_dashboards = sentry_sub.add_parser("apply-dashboards")
    apply_dashboards.add_argument("--dry-run", action="store_true")

    memory = sub.add_parser("memory", help="Shared memory commands.")
    memory_sub = memory.add_subparsers(dest="memory_command")
    import_cm = memory_sub.add_parser("import-claude-mem")
    import_cm.add_argument("--source", type=Path)
    search = memory_sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    context = memory_sub.add_parser("context")
    context.add_argument("--cwd", required=True)
    context.add_argument("--agent", choices=["codex", "claude-code", "pi"], required=True)
    context.add_argument("--limit", type=int, default=12)
    summarize = memory_sub.add_parser("summarize-session")
    summarize.add_argument("session_id")
    memory_sub.add_parser("rebuild-fts")

    dashboard = sub.add_parser("dashboard", help="Run local dashboard.")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    config = get_config()

    if args.command == "bridge":
        store = StateStore(config.state_path)
        if args.reset_state:
            backup = store.reset()
            if backup:
                log(f"moved existing state to {backup}")
        state = store.load()
        memory_store = MemoryStore(config.memory_db_path)
        sink = SentrySink(config, dry_run=args.dry_run)
        return run_bridge_loop(
            config,
            sink,
            memory_store,
            state,
            store.save,
            loop=args.loop,
            once=args.once or not args.loop,
            backfill_minutes=args.backfill_minutes,
        )

    if args.command == "status":
        return cmd_status(config)

    if args.command == "self-test":
        return cmd_self_test(config, dry_run=args.dry_run)

    if args.command == "backfill":
        return cmd_backfill(config, minutes=args.minutes, dry_run=args.dry_run, update_state=args.update_state)

    if args.command == "install-launchd":
        path = install_launchd(load=not args.no_load)
        print(path)
        return 0

    if args.command == "start-launchd":
        path = start_launchd()
        print(path)
        return 0

    if args.command == "stop-launchd":
        stop_launchd()
        print("stopped")
        return 0

    if args.command == "sentry" and args.sentry_command == "apply-dashboards":
        client = SentryDashboardClient(config)
        try:
            results = client.apply(dry_run=args.dry_run)
        except RuntimeError as exc:
            print(str(exc))
            return 2
        print(json.dumps([result.__dict__ for result in results], indent=2, default=str))
        return 0 if all(not r.status.startswith("failed") for r in results) else 1

    if args.command == "memory":
        return cmd_memory(config, args)

    if args.command == "dashboard":
        run_dashboard(config.memory_db_path, args.host, args.port)
        return 0

    parser.print_help()
    return 64


def cmd_status(config: Any) -> int:
    state = StateStore(config.state_path).load()
    memory = MemoryStore(config.memory_db_path)
    try:
        counts = memory.counts()
        usage_24h = memory.usage_rollup(hours=24, top_n=5)
    except Exception as exc:
        counts = {"error": str(exc)}
        usage_24h = {"error": str(exc)}
    status = {
        "version": VERSION,
        "config_path": str(config.config_path),
        "legacy_config_path": str(config.legacy_config_path),
        "state_path": str(config.state_path),
        "memory_db_path": str(config.memory_db_path),
        "pi_suggester_glob": config.pi_suggester_glob,
        "dsn_configured": bool(config.sentry_dsn),
        "sentry_org": config.sentry_org,
        "sentry_project": config.sentry_project,
        "initialized_at": state.get("initialized_at"),
        "codex_logs_last_id": state.get("codex_logs_last_id"),
        "codex_threads_last_updated_ms": state.get("codex_threads_last_updated_ms"),
        "claude_files_tracked": len(state.get("claude_files", {})),
        "pi_files_tracked": len(state.get("pi_files", {})),
        "memory_counts": counts,
        "usage_24h": usage_24h,
        "launchd": launchd_status().splitlines()[:8],
    }
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


def cmd_self_test(config: Any, dry_run: bool) -> int:
    sink = SentrySink(config, dry_run=dry_run)
    if not sink.configure():
        log("SENTRY_DSN is not configured; cannot send self-test")
        return 2
    marker = f"agent-vm-self-test-{int(time.time())}-{os.getpid()}"
    from .model import NormalizedTrace

    for agent in ("claude-code", "codex", "pi"):
        trace = NormalizedTrace(
            agent=agent,
            kind="self_test",
            timestamp=utc_now(),
            source_event_id=f"{agent}:{marker}",
            session_id=marker,
            project="coding-agent-sentry-observability",
            cwd=str(Path.cwd()),
            success=True,
            measurements={"self_test": 1},
            tags={"marker": marker, "vm_host": f"host:{short_hash(socket.gethostname())}"},
            extra={"marker": marker, "purpose": "manual Sentry ingestion verification", "bridge_version": VERSION},
        )
        sink.capture(trace)
    sink.flush(timeout=30)
    log(f"self-test sent marker={marker}")
    return 0


def cmd_backfill(config: Any, minutes: int, dry_run: bool, update_state: bool) -> int:
    if update_state:
        store = StateStore(config.state_path)
        state = store.load()
        save = store.save
    else:
        temp = tempfile.NamedTemporaryFile(prefix="agent-vm-backfill-state-", suffix=".json", delete=True)
        temp.close()
        temp_path = Path(temp.name)
        store = StateStore(temp_path)
        state = empty_state()
        save = store.save
    sink = SentrySink(config, dry_run=dry_run)
    memory_store = MemoryStore(config.memory_db_path)
    result = run_bridge_loop(config, sink, memory_store, state, save, loop=False, once=True, backfill_minutes=minutes)
    if result != 0:
        return result
    return export_sentry_usage_rollups(config, memory_store, minutes, dry_run=dry_run)


def export_sentry_usage_rollups(config: Any, memory_store: MemoryStore, minutes: int, dry_run: bool = False) -> int:
    sink = SentrySink(config, dry_run=dry_run)
    if not sink.configure():
        log("SENTRY_DSN is not configured; skipping Sentry usage rollups.")
        return 0

    rows = _usage_rows(memory_store, minutes)
    if not rows:
        log("no usage rows found for Sentry rollup export")
        return 0

    totals = _empty_usage_totals()
    by_agent: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    by_project: dict[str, dict[str, Any]] = {}
    sessions: set[str] = set()
    now = utc_now()
    min_sentry_timestamp = now - timedelta(days=4)
    clamped = 0
    sent = 0

    for row in rows:
        measurements = row["measurements"]
        original_timestamp = row["timestamp"]
        timestamp = original_timestamp
        if timestamp < min_sentry_timestamp:
            timestamp = min_sentry_timestamp + timedelta(seconds=clamped)
            clamped += 1
        trace = _usage_trace(
            row,
            measurements,
            "event",
            timestamp=timestamp,
            original_timestamp=original_timestamp,
        )
        sink.capture(trace)
        sent += 1

        session_id = row["session_id"]
        if session_id:
            sessions.add(str(session_id))
        _add_usage(totals, measurements)
        _add_usage(by_agent.setdefault(row["agent"], _empty_usage_totals()), measurements)
        _add_usage(by_model.setdefault(row["model"], _empty_usage_totals()), measurements)
        _add_usage(by_project.setdefault(row["project"], _empty_usage_totals()), measurements)
        if sent % 500 == 0:
            sink.flush(timeout=90)

    rollup_timestamp = now
    sink.capture(
        _usage_trace(
            _rollup_row("total", agent="all", model="all", project="all"),
            totals,
            "total",
            timestamp=rollup_timestamp,
        )
    )
    for agent, measurements in by_agent.items():
        sink.capture(
            _usage_trace(
                _rollup_row("agent", agent=agent, model="all", project="all"),
                measurements,
                "agent",
                timestamp=rollup_timestamp,
            )
        )
    for model, measurements in by_model.items():
        sink.capture(
            _usage_trace(
                _rollup_row("model", agent="all", model=model, project="all"),
                measurements,
                "model",
                timestamp=rollup_timestamp,
            )
        )
    for project, measurements in by_project.items():
        sink.capture(
            _usage_trace(
                _rollup_row("project", agent="all", model="all", project=project),
                measurements,
                "project",
                timestamp=rollup_timestamp,
            )
        )
    sink.flush(timeout=240)
    log(
        "exported Sentry usage rollups: "
        f"events={sent} sessions={len(sessions)} clamped={clamped} "
        f"tokens={int(round(totals.get('total_tokens') or 0))} cost_usd={round(float(totals.get('cost_usd') or 0), 6)}"
    )
    return 0


def _usage_rows(memory_store: MemoryStore, minutes: int) -> list[dict[str, Any]]:
    cutoff_epoch = max(int(time.time()) - minutes * 60, 0)
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    memory_store.initialize()
    with closing(memory_store.connect()) as conn:
        records = conn.execute(
            """
            select e.id, e.kind, e.title, e.level, e.timestamp, e.timestamp_epoch, e.project, e.model,
                   e.success, e.source_event_id, e.measurements_json, e.extra_json, e.duration_ms,
                   a.name as agent, s.external_session_id, s.cwd
            from events e
            join agents a on a.id = e.agent_id
            left join agent_sessions s on s.id = e.session_db_id
            where coalesce(e.timestamp_epoch, 0) >= ?
            order by e.timestamp_epoch asc, e.id asc
            """,
            (cutoff_epoch,),
        ).fetchall()
    for record in records:
        if record["kind"] == "thread_update":
            continue
        measurements = _usage_measurements(_json_object(record["measurements_json"]))
        if not any(measurements.get(key) for key in USAGE_SUM_KEYS):
            continue
        extra = _json_object(record["extra_json"])
        usage_key = _usage_identity(record["agent"], record["kind"], record["source_event_id"], extra)
        if usage_key in seen:
            continue
        seen.add(usage_key)
        rows.append(
            {
                "agent": record["agent"],
                "kind": record["kind"],
                "level": record["level"] or "info",
                "timestamp": parse_timestamp(record["timestamp"])
                or datetime.fromtimestamp(record["timestamp_epoch"], timezone.utc),
                "project": record["project"] or "unknown",
                "model": record["model"] or ("gpt-5.5" if record["agent"] == "pi" else "unknown"),
                "success": None if record["success"] is None else bool(record["success"]),
                "source_event_id": record["source_event_id"],
                "duration_ms": record["duration_ms"],
                "session_id": str(record["external_session_id"] or record["source_event_id"] or record["id"]),
                "cwd": record["cwd"],
                "measurements": measurements,
                "usage_key": usage_key,
            }
        )
    return rows


def _rollup_row(rollup: str, *, agent: str, model: str, project: str) -> dict[str, str]:
    return {
        "agent": agent,
        "model": model,
        "project": project,
        "session_id": f"rollup:{USAGE_SCHEMA}:{rollup}:{agent}:{model}:{project}",
    }


def _usage_trace(
    row: dict[str, Any],
    measurements: dict[str, float],
    rollup: str,
    *,
    timestamp: datetime,
    original_timestamp: datetime | None = None,
) -> NormalizedTrace:
    model = str(row.get("model") or "unknown")
    agent = str(row.get("agent") or "all")
    return NormalizedTrace(
        agent=agent,
        kind=f"usage_v{USAGE_SCHEMA.rsplit('_v', 1)[-1]}",
        timestamp=timestamp,
        level=str(row.get("level") or "info"),
        source_event_id=f"{USAGE_SCHEMA}:{rollup}:{row.get('usage_key') or row.get('session_id')}",
        session_id=str(row.get("session_id") or f"rollup:{USAGE_SCHEMA}:{rollup}"),
        project=str(row.get("project") or "unknown"),
        cwd=row.get("cwd") if isinstance(row.get("cwd"), str) else None,
        model=model if model != "all" else None,
        provider=_provider_for_model(model, agent),
        duration_ms=float(row.get("duration_ms") or 1.0),
        success=row.get("success") if isinstance(row.get("success"), bool) else None,
        measurements=measurements,
        tags={"usage_rollup": rollup, "source_kind": str(row.get("kind") or rollup)},
        extra={
            "usage_rollup": {
                "rollup": rollup,
                "original_timestamp": original_timestamp.isoformat() if original_timestamp else None,
            }
        },
    )


def _empty_usage_totals() -> dict[str, float]:
    return {key: 0.0 for key in USAGE_SUM_KEYS}


def _add_usage(total: dict[str, float], measurements: dict[str, float]) -> None:
    for key in USAGE_SUM_KEYS:
        total[key] += float(measurements.get(key) or 0)


def _provider_for_model(model: str, agent: str) -> str:
    value = model.lower()
    if value.startswith("gpt-"):
        return "openai"
    if value.startswith("claude"):
        return "anthropic"
    return agent


def cmd_memory(config: Any, args: Any) -> int:
    store = MemoryStore(config.memory_db_path)
    if args.memory_command == "import-claude-mem":
        source = args.source or config.claude_mem_db
        result = store.import_claude_mem(source)
        print(json.dumps(result.__dict__, indent=2))
        return 0
    if args.memory_command == "search":
        rows = store.search(args.query, limit=args.limit)
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if args.memory_command == "context":
        print(store.context(args.cwd, args.agent, limit=args.limit))
        return 0
    if args.memory_command == "summarize-session":
        print(store.summarize_session(args.session_id))
        return 0
    if args.memory_command == "rebuild-fts":
        store.initialize()
        store.rebuild_fts()
        print("rebuilt FTS")
        return 0
    print("missing memory command")
    return 64
