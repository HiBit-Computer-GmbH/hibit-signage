from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from hibit_player.config import data_dir, install_dir

_DONE = "=== Player-Update fertig ==="
_FAILED = "=== Update fehlgeschlagen ==="
_MARK = "=== Update angefordert ==="


def log_path() -> Path:
    return data_dir() / "update.log"


def status_path() -> Path:
    return data_dir() / "update.status"


def script_path() -> Path:
    return Path("/usr/local/sbin/hibit-update-player")


def read_log() -> str:
    path = log_path()
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    index = text.rfind(_MARK)
    if index >= 0:
        text = text[index:]
    return text[-20000:]


def snapshot() -> dict:
    """Zustand des letzten Update-Laufs: idle, running, done oder failed."""
    text = read_log()
    raw = _read_status()
    if _FAILED in text:
        return {"log": text, "state": "failed"}
    if _DONE in text:
        return {"log": text, "state": "done"}
    if raw == "running" or raw.startswith("running:"):
        pid = _pid(raw)
        if pid and not _pid_alive(pid):
            return {"log": text, "state": "failed"}
        if raw == "running" and _status_age() > 180:
            return {"log": text, "state": "failed"}
        return {"log": text, "state": "running"}
    return {"log": text, "state": "idle"}


def launch() -> str:
    """Start the root-owned update script. The app itself does not pull Git as the service user."""
    script = script_path()
    if not script.exists():
        raise RuntimeError(
            "Das Update-Skript fehlt. Es wird durch die Installation nach /usr/local/sbin/hibit-update-player gelegt."
        )
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Updates über das Installationsskript sind für das Linux-Gerät vorgesehen.")
    if snapshot()["state"] == "running":
        raise RuntimeError("Ein Update läuft bereits.")
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{_MARK}\n")
        handle.flush()
        proc = subprocess.Popen(
            ["sudo", "-n", str(script)],
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(install_dir()),
        )
    if proc.poll() is not None:
        _write_status("failed")
        raise RuntimeError("Das Update konnte nicht gestartet werden.")
    _write_status(f"running:{proc.pid}")
    return "Update wurde gestartet. Der Player startet danach neu."


def _read_status() -> str:
    try:
        return status_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_status(value: str) -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")


def _pid(raw: str) -> int:
    if ":" not in raw:
        return 0
    try:
        return int(raw.split(":", 1)[1])
    except ValueError:
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _status_age() -> float:
    try:
        return time.time() - status_path().stat().st_mtime
    except OSError:
        return 0
