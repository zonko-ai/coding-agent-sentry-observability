# Security Policy

`coding-agents-mem` processes local agent telemetry that can contain sensitive prompts, file paths, tool arguments, and secrets. Please report security issues privately before opening a public issue.

## Supported Versions

The project is currently pre-1.0. Security fixes target the latest `main` branch.

## Reporting a Vulnerability

If this repository has a GitHub Security Advisory enabled, use that channel. Otherwise, contact the maintainers privately through the repository owner's published contact method.

Please include:

- affected version or commit
- affected command or data source
- reproduction steps
- whether raw text export was enabled
- whether Sentry export was enabled

## Security Expectations

- Raw text export must remain opt-in.
- Sentry tags and extras must not expose common secrets.
- Local source readers must be read-only.
- Install scripts must not upload telemetry by default.
