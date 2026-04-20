from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def empty_state() -> dict[str, Any]:
    return {"version": 1, "claude_files": {}, "codex_threads_last_updated_ms": 0, "codex_logs_last_id": 0}


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_state()
        try:
            return json.loads(self.path.read_text())
        except Exception:
            backup = self.path.with_suffix(f".corrupt.{int(time.time())}.json")
            self.path.replace(backup)
            return empty_state()

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def reset(self) -> Path | None:
        if not self.path.exists():
            return None
        backup = self.path.with_suffix(f".bak.{int(time.time())}.json")
        self.path.replace(backup)
        return backup

