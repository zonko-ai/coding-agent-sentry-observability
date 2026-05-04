#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON:-python3}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

"$PYTHON_BIN" -m venv .venv
. .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

python - <<'PY'
from pathlib import Path
from shutil import copyfile

from agent_vm_observability.config import CONFIG_PATH, LEGACY_CONFIG_PATH, OLD_CONFIG_PATH

CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
if not CONFIG_PATH.exists():
    for candidate in (OLD_CONFIG_PATH, LEGACY_CONFIG_PATH):
        if candidate.exists():
            copyfile(candidate, CONFIG_PATH)
            break
    else:
        copyfile(Path(".env.example"), CONFIG_PATH)
PY

cat <<'MSG'
Installed coding-agent-sentry-observability.

Next steps:
  . .venv/bin/activate
  agent-vm status
  agent-vm backfill --minutes 30 --dry-run

Edit ~/.config/coding-agent-sentry-observability/env to enable Sentry export.
MSG
