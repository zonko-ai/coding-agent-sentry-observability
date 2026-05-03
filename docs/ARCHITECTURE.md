# Architecture

`coding-agents-mem` is organized around small source adapters that normalize records into a common trace shape.

## Data Flow

```text
Codex SQLite      Claude JSONL      Pi NDJSON
     |                |                |
     v                v                v
              AgentIngestor
                    |
                    v
             NormalizedTrace
              /           \
             v             v
       SQLite memory     Sentry
          store          events
             |
             v
      local dashboard
```

## Modules

- `config.py`: resolves environment variables and default source paths.
- `state.py`: stores incremental watermarks for log tails and database cursors.
- `ingest.py`: reads Codex, Claude Code, and Pi sources and normalizes events.
- `model.py`: defines `NormalizedTrace` and Sentry tag/extra helpers.
- `memory.py`: owns the SQLite schema, trace persistence, usage rollups, search, and context output.
- `sentry_sink.py`: sends sanitized events and transactions to Sentry.
- `sentry_dashboards.py`: provisions Sentry dashboards from Python specs.
- `local_dashboard.py`: renders an HTML dashboard backed by SQLite.
- `launchd.py`: installs and controls the macOS LaunchAgent.

## Source Adapters

Each adapter should:

- read source data without mutating it
- maintain a stable source event id
- map token and cost fields into measurements
- avoid raw text unless explicitly requested
- record enough metadata for debugging without leaking secrets

## State

The bridge keeps watermarks in `AGENT_VM_STATE`, defaulting to:

`~/.local/state/agent-vm-observability/state.json`

Backfills use isolated state unless `--update-state` is provided.

## Storage

The local memory database is SQLite with WAL mode enabled. It stores:

- agents
- sessions
- turns
- events
- tool calls
- observations
- session summaries
- memories

FTS indexes power memory search and project context output.
