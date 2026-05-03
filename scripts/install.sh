#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON:-python3}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

"$PYTHON_BIN" -m venv .venv
. .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

mkdir -p "$HOME/.config/agent-vm-observability"
if [ ! -f "$HOME/.config/agent-vm-observability/env" ]; then
  cp .env.example "$HOME/.config/agent-vm-observability/env"
fi

cat <<'MSG'
Installed coding-agents-mem.

Next steps:
  . .venv/bin/activate
  agent-vm status
  agent-vm backfill --minutes 30 --dry-run

Edit ~/.config/agent-vm-observability/env to enable Sentry export.
MSG
