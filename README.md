# Agent VM Observability

Agent VM Observability packages the local Sentry bridge and central memory store for agent usage on this machine. It watches Claude Code and Codex local telemetry, exports normalized traces to Sentry, and keeps a shared SQLite memory database that can import the existing `claude-mem` database without mutating it.

## What It Tracks

- Claude Code JSONL records from `~/.claude/projects/**/*.jsonl`
- Codex OTel logs from `~/.codex/logs_2.sqlite`
- Codex thread metadata from `~/.codex/state_5.sqlite`
- Normalized sessions, turns, events, tool calls, observations, summaries, and memories in `~/.agent-vm-observability/memory.db`

Raw message text is disabled by default. The bridge stores lengths, hashes, metadata, model/tool/timing fields, and token/cache counters. Set `AGENT_SENTRY_INCLUDE_TEXT=1` only if raw redacted text should be exported.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

Existing Sentry DSN config remains compatible at `~/.config/agent-sentry/env`. New config is read from `~/.config/agent-vm-observability/env` as an override. Do not commit either file.

## CLI

```bash
agent-vm bridge --loop
agent-vm status
agent-vm self-test
agent-vm backfill --minutes 30
agent-vm install-launchd
agent-vm start-launchd
agent-vm stop-launchd
agent-vm sentry apply-dashboards --dry-run
agent-vm memory import-claude-mem
agent-vm memory search "hero dashboard"
agent-vm memory context --cwd /path/to/repo --agent codex
agent-vm memory summarize-session <session-id>
agent-vm memory rebuild-fts
agent-vm dashboard --port 8765
```

`agent-vm sentry apply-dashboards` requires `SENTRY_AUTH_TOKEN` with dashboard/project/event read-write permissions. It never stores the token.

## Local Service

`agent-vm install-launchd` writes a LaunchAgent that runs:

```bash
python -m agent_vm_observability bridge --loop
```

The packaged service label is `com.sahan.agent-vm-observability`. The older `com.sahan.agent-sentry-bridge` service can continue running until you switch over.
The LaunchAgent sets a 4096-file soft limit so high-volume trace batches do not hit macOS's default per-job limit.

## Development

```bash
python -m pytest
python -m agent_vm_observability status
python -m agent_vm_observability backfill --minutes 5 --dry-run
```
