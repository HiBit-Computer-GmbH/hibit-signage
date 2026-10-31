from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def script_path() -> Path:
    return Path("/usr/local/sbin/hibit-power")


def launch(action: str) -> str:
    if action not in {"reboot", "poweroff"}:
        raise RuntimeError("Unbekannte Aktion.")
    script = script_path()
    if not sys.platform.startswith("linux") or not script.exists():
        raise RuntimeError("Neustart und Herunterfahren gibt es nur auf dem installierten Pi.")
    subprocess.Popen(
        ["sudo", "-n", str(script), action],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if action == "reboot":
        return "Neustart wurde gestartet."
    return "Das Gerät fährt herunter."
