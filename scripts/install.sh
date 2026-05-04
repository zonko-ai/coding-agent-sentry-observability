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

BACKFILL_MINUTES="${AGENT_VM_INSTALL_BACKFILL_MINUTES:-10080}"
if [[ "${AGENT_VM_INSTALL_SKIP_BACKFILL:-0}" =~ ^(1|true|yes|on)$ ]]; then
  echo "Skipping initial backfill because AGENT_VM_INSTALL_SKIP_BACKFILL is set."
else
  echo "Backfilling the last ${BACKFILL_MINUTES} minutes of agent usage..."
  if ! agent-vm backfill --minutes "$BACKFILL_MINUTES"; then
    cat >&2 <<'WARN'
Warning: initial usage backfill failed. Installation succeeded, but history was not imported.
Run this after fixing configuration/source access:
  agent-vm backfill --minutes 10080
WARN
  fi
fi

cat <<'MSG'
Installed coding-agent-sentry-observability.

Next steps:
  . .venv/bin/activate
  agent-vm status
  agent-vm dashboard --port 8765

The installer imports the last 7 days by default. Set
AGENT_VM_INSTALL_SKIP_BACKFILL=1 to skip or AGENT_VM_INSTALL_BACKFILL_MINUTES
for a different window.

Edit ~/.config/coding-agent-sentry-observability/env to enable Sentry export.
MSG
