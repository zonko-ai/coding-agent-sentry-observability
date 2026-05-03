# Contributing

Thanks for taking the time to improve `coding-agents-mem`.

## Development Setup

```bash
./scripts/install.sh
. .venv/bin/activate
python -m pytest
```

## Pull Request Checklist

- Keep telemetry readers read-only.
- Do not commit local databases, logs, `.pi/`, `.venv/`, or config files.
- Add or update tests for behavior changes.
- Keep raw text export opt-in.
- Redact or hash sensitive values before sending anything to Sentry.
- Update docs when adding a command, source, or environment variable.

## Coding Guidelines

- Prefer small source-specific adapters that produce `NormalizedTrace`.
- Preserve existing environment-variable compatibility where practical.
- Treat local agent logs as sensitive data.
- Keep public defaults neutral and non-personal.

## Running Checks

```bash
python -m pytest
python -m build --sdist --wheel
agent-vm backfill --minutes 5 --dry-run
```
