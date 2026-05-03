# Configuration

`coding-agents-mem` reads shell-style `KEY=value` files without executing shell code.

Read order:

1. `~/.config/agent-sentry/env` for backward compatibility
2. `~/.config/agent-vm-observability/env`
3. process environment variables

Later values in the process environment override values loaded from files.

## Core Paths

| Variable | Default | Description |
| --- | --- | --- |
| `AGENT_VM_MEMORY_DB` | `~/.agent-vm-observability/memory.db` | SQLite store for normalized sessions, events, tools, summaries, and memories |
| `AGENT_VM_STATE` | `~/.local/state/agent-vm-observability/state.json` | Watermarks for incremental ingestion |
| `AGENT_VM_CODEX_LOGS_DB` | `~/.codex/logs_2.sqlite` | Codex OTel log database |
| `AGENT_VM_CODEX_STATE_DB` | `~/.codex/state_5.sqlite` | Codex thread metadata database |
| `AGENT_VM_CLAUDE_GLOB` | `~/.claude/projects/**/*.jsonl` | Claude Code session JSONL glob |
| `AGENT_VM_PI_SUGGESTER_GLOB` | `~/.pi/suggester` | Pi suggester root, `events.ndjson`, or glob |

`AGENT_VM_PI_SUGGESTER_GLOB` may point to a root such as `~/.pi/suggester`, a project-local root such as `/path/to/project/.pi/suggester`, or a direct `events.ndjson` file.

## Sentry

| Variable | Default | Description |
| --- | --- | --- |
| `SENTRY_DSN` | unset | Enables Sentry export |
| `SENTRY_ORG` | unset | Required for dashboard provisioning |
| `SENTRY_PROJECT` | `agent-vm-usage` | Sentry project slug |
| `SENTRY_PROJECT_ID` | unset | Optional numeric project id for dashboards |
| `SENTRY_AUTH_TOKEN` | unset | Required only for `agent-vm sentry apply-dashboards` |
| `SENTRY_TRACES_SAMPLE_RATE` | `1.0` | Sentry transaction sample rate |

## Runtime

| Variable | Default | Description |
| --- | --- | --- |
| `AGENT_VM_MAX_BATCH` | `250` | Maximum records processed per source per poll |
| `AGENT_VM_POLL_SECONDS` | `15` | Bridge loop sleep interval |
| `AGENT_VM_RECORD_MEMORY` | `1` | Whether normalized traces are written to SQLite |
| `AGENT_SENTRY_INCLUDE_TEXT` | `0` | Opt in to redacted raw text export |
| `AGENT_VM_SENTRY_SET_USER` | `0` | Opt in to setting the local OS user in Sentry |

## Pricing Overrides

Built-in pricing rules cover common OpenAI and Anthropic model names at the time the rule was added. Override or extend pricing with:

- `AGENT_VM_MODEL_PRICING_FILE=/path/to/pricing.json`
- `AGENT_VM_MODEL_PRICING_JSON='[...]'`

Each rule should include:

```json
{
  "name": "example-model",
  "pattern": "^example-model$",
  "provider": "openai",
  "input_per_mtok_usd": 1.0,
  "output_per_mtok_usd": 5.0,
  "cached_input_per_mtok_usd": 0.1,
  "source": "custom"
}
```
