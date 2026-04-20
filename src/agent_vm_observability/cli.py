from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Any

from . import VERSION
from .config import get_config, load_env_files
from .ingest import AgentIngestor, log, run_bridge_loop
from .launchd import install_launchd, launchd_status, start_launchd, stop_launchd
from .local_dashboard import run_dashboard
from .memory import MemoryStore
from .sentry_dashboards import SentryDashboardClient
from .sentry_sink import SentrySink
from .state import StateStore, empty_state
from .timeutil import utc_now


def main(argv: list[str] | None = None) -> int:
    load_env_files()
    parser = argparse.ArgumentParser(description="Local observability and memory for agent usage on this VM.")
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
    context.add_argument("--agent", choices=["codex", "claude-code"], required=True)
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
    except Exception as exc:
        counts = {"error": str(exc)}
    status = {
        "version": VERSION,
        "config_path": str(config.config_path),
        "legacy_config_path": str(config.legacy_config_path),
        "state_path": str(config.state_path),
        "memory_db_path": str(config.memory_db_path),
        "dsn_configured": bool(config.sentry_dsn),
        "sentry_org": config.sentry_org,
        "sentry_project": config.sentry_project,
        "initialized_at": state.get("initialized_at"),
        "codex_logs_last_id": state.get("codex_logs_last_id"),
        "codex_threads_last_updated_ms": state.get("codex_threads_last_updated_ms"),
        "claude_files_tracked": len(state.get("claude_files", {})),
        "memory_counts": counts,
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

    for agent in ("claude-code", "codex"):
        trace = NormalizedTrace(
            agent=agent,
            kind="self_test",
            timestamp=utc_now(),
            source_event_id=f"{agent}:{marker}",
            session_id=marker,
            project="coding-agents-mem",
            cwd=str(Path.cwd()),
            success=True,
            measurements={"self_test": 1},
            tags={"marker": marker, "host": socket.gethostname()},
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
    return run_bridge_loop(config, sink, memory_store, state, save, loop=False, once=True, backfill_minutes=minutes)


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
