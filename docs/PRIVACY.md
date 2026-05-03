# Privacy

Agent telemetry can contain sensitive data. Treat local databases, logs, and exports as private operational data.

## Default Behavior

By default, `coding-agents-mem`:

- does not export raw prompt or response text
- hashes text previews and first-user-message text
- records text lengths for debugging and aggregate analysis
- redacts common secret and email patterns
- hashes local `cwd` and `repo` Sentry tags
- does not set the local OS user in Sentry

The local SQLite database stores normalized operational metadata and may include local paths. Keep it on trusted storage.

## Opting Into Text Export

Set `AGENT_SENTRY_INCLUDE_TEXT=1` only if you understand the risk. Even with this enabled, text is passed through the redaction layer before export, but no redactor is perfect.

Recommended practice:

1. Start with `agent-vm backfill --minutes 5 --dry-run`.
2. Inspect the dry-run output.
3. Enable Sentry only after validating the data shape.

## Sentry Data

Sentry receives normalized events and transactions. Measurements may include token counts, duration, cost estimates, and tool activity. Tags include agent, model, project, kind, and hashed path identifiers.

`AGENT_VM_SENTRY_SET_USER=1` opts into setting the local OS user as the Sentry user id. Leave it unset for public or shared deployments.

## Local Data

The SQLite memory store defaults to:

`~/.agent-vm-observability/memory.db`

It is not encrypted by this package. Use filesystem permissions, disk encryption, and backups according to your own security requirements.
