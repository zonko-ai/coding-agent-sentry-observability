from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

LABEL = "com.zonko.coding-agent-sentry-observability"
LEGACY_LABELS = ("com.sahan.agent-vm-observability", "com.sahan.coding-agent-sentry-observability")
LOG_DIR = Path.home() / "Library/Logs/coding-agent-sentry-observability"


def plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def plist_payload() -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "agent_vm_observability", "bridge", "--loop"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(Path.home()),
        "StandardOutPath": str(LOG_DIR / "bridge.out.log"),
        "StandardErrorPath": str(LOG_DIR / "bridge.err.log"),
        "SoftResourceLimits": {"NumberOfFiles": 4096},
    }


def install_launchd(load: bool = True) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(plist_payload()))
    if load:
        stop_launchd()
        start_launchd()
    return path


def start_launchd() -> Path:
    path = plist_path()
    if not path.exists():
        install_launchd(load=False)
    domain = f"gui/{os.getuid()}"
    time.sleep(0.5)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        time.sleep(1)
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=False)
    return path


def stop_launchd() -> None:
    domain = f"gui/{os.getuid()}"
    for label in (LABEL, *LEGACY_LABELS):
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launchd_status() -> str:
    domain = f"gui/{os.getuid()}"
    for label in (LABEL, *LEGACY_LABELS):
        result = subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout
    return "launchd service is not loaded"
