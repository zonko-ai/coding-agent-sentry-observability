# coding-agents-sentry-observability

Local observability for coding agents. Collect Pi, Claude Code, and Codex activity into one SQLite database, inspect it in a local dashboard, and optionally export normalized traces to Sentry.

Sentry is optional. The project works in local-only mode by default and keeps raw prompt/tool text out of storage unless you explicitly opt in.

## Why this exists

Running multiple coding agents locally makes it hard to answer basic operational questions:

- Which agents ran, in which repositories, and when?
- Which models and tools are being used most?
- How many tokens and estimated dollars are being spent?
- Which sessions failed or produced error-level traces?
- What happened in a session without opening every vendor-specific log format?

`coding-agents-sentry-observability` turns those local logs into a common trace model and a queryable memory database.

## Supported sources

| Source | Default location | What is captured |
| --- | --- | --- |
| Pi | `~/.pi/agent/sessions/**/*.jsonl` | sessions, model changes, user/assistant turns, tool calls/results, subagent runs, usage/cost metadata |
| Claude Code | `~/.claude/projects/**/*.jsonl` | prompts, assistant turns, tool calls/results, model usage, cache/token counters |
| Codex | `~/.codex/logs_2.sqlite`, `~/.codex/state_5.sqlite` | OTel logs, thread updates, tool/model/timing/token metadata |

All sources are normalized into `~/.agent-vm-observability/memory.db`.

## Features

- **One local timeline** for Pi, Claude Code, and Codex activity.
- **Privacy-first defaults**: stores text lengths, hashes, metadata, tool names, model names, timings, token counters, and cost fields; raw text is disabled by default.
- **Local dashboard** for recent sessions, model mix, project spend, failure traces, tool activity, and memories.
- **Optional Sentry export** with bundled dashboard definitions.
- **Backfill support** for recent local history without advancing live state unless requested.
- **macOS LaunchAgent installer** for continuous background collection.
- **SQLite-first design** so data remains inspectable with standard tools.

## Quick start

```bash
git clone https://github.com/sahan-penakalapati/coding-agents-sentry-observability.git
cd coding-agents-sentry-observability
./scripts/install.sh
source .venv/bin/activate
agent-vm bridge --once --backfill-minutes 30
agent-vm dashboard --port 8765
```

Open <http://127.0.0.1:8765>.

To run continuously on macOS:

```bash
./scripts/install.sh --service
```

The CLI command remains `agent-vm` and runtime paths remain `agent-vm*` for compatibility with earlier local installs.

## Privacy model

Raw message and tool text is disabled by default:

```bash
AGENT_VM_INCLUDE_TEXT=0
```

With the default setting, the bridge records summaries such as text length, SHA-256 hashes, content block types, tool names, input keys, model metadata, token counters, cache counters, timings, exit codes, and cost fields. Set `AGENT_VM_INCLUDE_TEXT=1` only if you explicitly want redacted raw text in the local database and Sentry payloads.

Generated config is written with `chmod 600`. Do not commit local config, SQLite databases, `.pi/` runtime files, or exported session logs.

## Optional Sentry export

Local-only mode requires no Sentry configuration. To export traces, add a DSN:

```bash
./scripts/install.sh --dsn 'https://public@example.ingest.sentry.io/project-id'
```

Or edit `~/.config/agent-vm-observability/env`:

```bash
SENTRY_DSN=
SENTRY_ORG=your-org
SENTRY_PROJECT=agent-vm-usage
```

Apply bundled dashboards:

```bash
export SENTRY_AUTH_TOKEN=<token>
agent-vm sentry apply-dashboards --dry-run
agent-vm sentry apply-dashboards
```

The token is read from the environment and is not stored by this project.

## Common commands

```bash
agent-vm status
agent-vm bridge --once --backfill-minutes 60
agent-vm bridge --loop
agent-vm dashboard --port 8765
agent-vm memory search "dashboard decision"
agent-vm memory context --cwd /path/to/repo --agent pi
agent-vm memory summarize-session <session-id>
agent-vm memory rebuild-fts
```

## Configuration

The installer writes `~/.config/agent-vm-observability/env`. See `docs/env.example` for all common settings.

Useful overrides:

```bash
AGENT_VM_PI_GLOB=~/.pi/agent/sessions/**/*.jsonl
AGENT_VM_CLAUDE_GLOB=~/.claude/projects/**/*.jsonl
AGENT_VM_CODEX_LOGS_DB=~/.codex/logs_2.sqlite
AGENT_VM_CODEX_STATE_DB=~/.codex/state_5.sqlite
AGENT_VM_MEMORY_DB=~/.agent-vm-observability/memory.db
AGENT_VM_POLL_SECONDS=15
AGENT_VM_MAX_BATCH=250
```

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
python -m agent_vm_observability status
python -m agent_vm_observability backfill --minutes 5 --dry-run
```

## Documentation

- `docs/INSTALL.md` — installer options, service setup, and Sentry dashboard setup
- `docs/env.example` — configuration template
- `dashboards/sentry/agent-vm-observability.json` — bundled Sentry dashboard JSON

## License

MIT. See `LICENSE`.
