from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hibit_cloud.config import data_dir, install_dir


def log_path() -> Path:
    return data_dir() / "update.log"


def script_path() -> Path:
    return Path("/usr/local/sbin/hibit-update-cloud")


def read_log() -> str:
    path = log_path()
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-12000:]


def launch() -> str:
    script = script_path()
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not script.exists():
        raise RuntimeError(
            "Das Update-Skript fehlt. Es wird durch die Installation nach /usr/local/sbin/hibit-update-cloud gelegt."
        )
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Updates über das Installationsskript sind für den Linux-Server vorgesehen.")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n=== Update angefordert ===\n")
        subprocess.Popen(
            ["sudo", "-n", str(script)],
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(install_dir()),
        )
    return "Update der Cloud wurde gestartet. Die Konsole startet danach neu."
