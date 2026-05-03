# Install coding-agents-sentry-observability

`coding-agents-sentry-observability` collects local coding-agent activity into one SQLite database and, optionally, exports normalized traces to Sentry.

Supported sources today:

- Pi session JSONL records from `~/.pi/agent/sessions/**/*.jsonl`
- Claude Code JSONL records from `~/.claude/projects/**/*.jsonl`
- Codex OTel logs from `~/.codex/logs_2.sqlite`
- Codex thread metadata from `~/.codex/state_5.sqlite`

Sentry is optional. If `SENTRY_DSN` is unset, the bridge runs in local-only mode and still records to `~/.agent-vm-observability/memory.db`.

## Quick install from a clone

```bash
git clone https://github.com/<you>/coding-agents-sentry-observability.git
cd coding-agents-sentry-observability
./scripts/install.sh
```

This creates:

- a Python virtualenv at `./.venv`
- a config file at `~/.config/agent-vm-observability/env`
- an editable install of the `agent-vm` CLI

Then run a one-time backfill and open the local dashboard:

```bash
source .venv/bin/activate
agent-vm bridge --once --backfill-minutes 30
agent-vm dashboard --port 8765
```

Visit <http://127.0.0.1:8765>.

## Optional Sentry export

Pass a DSN during install:

```bash
./scripts/install.sh --dsn 'https://public@example.ingest.sentry.io/project-id'
```

Or edit `~/.config/agent-vm-observability/env` later:

```bash
SENTRY_DSN=
SENTRY_ORG=your-org
SENTRY_PROJECT=your-project
```

Apply the bundled Sentry dashboards after setting `SENTRY_AUTH_TOKEN`:

```bash
export SENTRY_AUTH_TOKEN=<token>
agent-vm sentry apply-dashboards --dry-run
agent-vm sentry apply-dashboards
```

## Background service on macOS

The installer can register a LaunchAgent:

```bash
./scripts/install.sh --service
```

Equivalent manual commands:

```bash
agent-vm install-launchd
agent-vm start-launchd
agent-vm stop-launchd
```

The service runs:

```bash
python -m agent_vm_observability bridge --loop
```

Logs are written under:

```text
~/Library/Logs/agent-vm-observability/
```

Linux service units are not packaged yet. On Linux, run the bridge manually or create a user-level systemd unit that executes:

```bash
/path/to/repo/.venv/bin/agent-vm bridge --loop
```

## Privacy defaults

Raw message and tool text is disabled by default:

```bash
AGENT_VM_INCLUDE_TEXT=0
```

With the default setting, the bridge stores text lengths, hashes, tool names, timing, model, token, cache, cost, and status metadata. Set `AGENT_VM_INCLUDE_TEXT=1` only if you explicitly want redacted raw text in the local DB/Sentry payloads.

The generated config is written with `chmod 600`. Do not commit local config, SQLite databases, or `.pi/` runtime/session files.

## Useful commands

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

## Installer options

```text
--service       Install and start the macOS launchd background service
--dev           Install test dependencies too
--dsn VALUE     Write VALUE as SENTRY_DSN in generated config
--config FILE   Config file path
--venv DIR      Virtualenv directory
--force-config  Overwrite an existing config file
--dry-run       Print actions without making changes
```
