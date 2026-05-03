from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

LABEL = "io.github.coding-agents-sentry-observability.agent-vm"


def plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def plist_payload() -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "agent_vm_observability", "bridge", "--loop"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(Path.home()),
        "StandardOutPath": str(Path.home() / "Library/Logs/agent-vm-observability/bridge.out.log"),
        "StandardErrorPath": str(Path.home() / "Library/Logs/agent-vm-observability/bridge.err.log"),
        "SoftResourceLimits": {"NumberOfFiles": 4096},
    }


def install_launchd(load: bool = True) -> Path:
    _require_launchd()
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    (Path.home() / "Library/Logs/agent-vm-observability").mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(plist_payload()))
    if load:
        stop_launchd()
        start_launchd()
    return path


def start_launchd() -> Path:
    _require_launchd()
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
    _require_launchd()
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launchd_status() -> str:
    if not _launchd_available():
        return "launchd is only available on macOS"
    domain = f"gui/{os.getuid()}"
    result = subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else "launchd service is not loaded"


def _launchd_available() -> bool:
    return sys.platform == "darwin" and shutil.which("launchctl") is not None


def _require_launchd() -> None:
    if not _launchd_available():
        raise RuntimeError("launchd service management is only available on macOS")
