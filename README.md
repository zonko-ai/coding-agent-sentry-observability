# coding-agent-sentry-observability

Local observability and shared memory for coding agents.

`coding-agent-sentry-observability` tails local agent telemetry, normalizes it into a shared SQLite store, and can mirror sanitized traces to Sentry. It is designed for developers who use multiple coding agents on the same workstation or VM and want one place to inspect usage, cost, sessions, tools, failures, and reusable context.

## Supported Agents

- Codex: OTel logs from `~/.codex/logs_2.sqlite` and thread metadata from `~/.codex/state_5.sqlite`
- Claude Code: JSONL records from `~/.claude/projects/**/*.jsonl`
- Pi: suggester events from `~/.pi/suggester/logs/events.ndjson`

Raw message text is disabled by default. The bridge records lengths, hashes, metadata, token counters, tool names, timing, and cost estimates. Sentry path tags are hashed by default.

## Install

```bash
git clone https://github.com/zonko-ai/coding-agent-sentry-observability.git
cd coding-agent-sentry-observability
./scripts/install.sh
```

The installer creates the config file and imports the last 7 days of local agent history by default (`10080` minutes). Override this with `AGENT_VM_INSTALL_BACKFILL_MINUTES=<minutes>` or skip it with `AGENT_VM_INSTALL_SKIP_BACKFILL=1`. If you enable Sentry after the first install, rerun `agent-vm backfill --minutes 10080` to mirror that history to Sentry too.

Manual setup:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

Create a local config file:

```bash
mkdir -p ~/.config/coding-agent-sentry-observability
cp .env.example ~/.config/coding-agent-sentry-observability/env
```

Set `SENTRY_DSN`, `SENTRY_ORG`, and `SENTRY_PROJECT_ID` only if you want Sentry export or dashboard provisioning. The local SQLite memory store works without Sentry.

## Quick Start

```bash
agent-vm status
agent-vm dashboard --port 8765
agent-vm bridge --loop
```

Open `http://127.0.0.1:8765` for the local dashboard.

## Why Ingestion and Local Storage?

Ingestion is the collector and normalizer. It reads raw telemetry from each agent source and converts Codex, Claude Code, and Pi records into one common shape: sessions, turns, model calls, tool calls, token counts, costs, failures, projects, and timing.

Local storage is the private source of truth. Normalized traces are written to SQLite so the local dashboard, usage rollups, memory search, context commands, deduplication, and backfills work even without Sentry.

```text
Agent logs -> ingestion -> normalized traces -> local SQLite
                                         -> optional sanitized Sentry export
```

If you only want Sentry dashboards, set `AGENT_VM_RECORD_MEMORY=0` to skip local SQLite writes. Keeping it enabled is recommended because it preserves a local, queryable history while Sentry remains the shared observability layer.

## Common Commands

```bash
agent-vm status
agent-vm self-test --dry-run
agent-vm backfill --minutes 30
agent-vm bridge --loop
agent-vm install-launchd
agent-vm start-launchd
agent-vm stop-launchd
agent-vm sentry apply-dashboards --dry-run
agent-vm memory search "dashboard"
agent-vm memory context --cwd /path/to/repo --agent codex
agent-vm memory context --cwd /path/to/repo --agent claude-code
agent-vm memory context --cwd /path/to/repo --agent pi
agent-vm memory summarize-session <session-id>
agent-vm memory rebuild-fts
```

## Configuration

The bridge reads environment variables from:

1. legacy config files from earlier releases, if present
2. `~/.config/coding-agent-sentry-observability/env` as the preferred path
3. the current shell environment

Useful variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SENTRY_DSN` | unset | Enables Sentry export when present |
| `SENTRY_ORG` | unset | Required for `agent-vm sentry apply-dashboards` |
| `SENTRY_PROJECT` | `agent-vm-usage` | Sentry project slug for status/config display |
| `SENTRY_PROJECT_ID` | unset | Optional project id for dashboard payloads |
| `AGENT_VM_MEMORY_DB` | `~/.coding-agent-sentry-observability/memory.db` | Shared SQLite database |
| `AGENT_VM_CODEX_LOGS_DB` | `~/.codex/logs_2.sqlite` | Codex OTel source |
| `AGENT_VM_CODEX_STATE_DB` | `~/.codex/state_5.sqlite` | Codex thread source |
| `AGENT_VM_CLAUDE_GLOB` | `~/.claude/projects/**/*.jsonl` | Claude Code JSONL source |
| `AGENT_VM_PI_SUGGESTER_GLOB` | `~/.pi/suggester` | Pi suggester root or glob |
| `AGENT_SENTRY_INCLUDE_TEXT` | `0` | Opt in to redacted raw text export |
| `AGENT_VM_RECORD_MEMORY` | `1` | Write normalized traces to SQLite |

More detail: [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Privacy Model

The default posture is local-first and text-minimizing:

- raw prompt and response text is not exported unless explicitly enabled
- text previews are stored as length plus hash
- common secrets and emails are redacted
- Sentry `cwd` and `repo` tags are hashed
- local SQLite keeps richer operational context on the developer machine

See [docs/PRIVACY.md](docs/PRIVACY.md) before enabling text export.

## Architecture

At a high level:

1. `config.py` resolves local source paths and runtime options.
2. `ingest.py` tails Codex, Claude Code, and Pi sources with per-source watermarks.
3. `model.py` normalizes records into `NormalizedTrace`.
4. `memory.py` stores sessions, events, tools, summaries, and memories in SQLite.
5. `sentry_sink.py` mirrors sanitized events and transactions to Sentry.
6. `local_dashboard.py` renders a local HTML dashboard from SQLite.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Development

```bash
. .venv/bin/activate
python -m pytest
python -m build --sdist --wheel
python -m agent_vm_observability status
python -m agent_vm_observability backfill --minutes 5 --dry-run
```

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and keep changes privacy-conscious by default.

## License

MIT. See [LICENSE](LICENSE).
