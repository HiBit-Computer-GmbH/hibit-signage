"""Speichert das Boot-Logo und richtet den Plymouth-Splash auf dem Pi ein."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hibit_player.config import data_dir

MAX_LOGO = 1_500_000
PNG = b"\x89PNG\r\n\x1a\n"


def logo_path() -> Path:
    return data_dir() / "boot-logo.png"


def has_custom_logo() -> bool:
    path = logo_path()
    return path.is_file() and path.stat().st_size > 0


def save_logo(data: bytes) -> None:
    if len(data) < 32 or len(data) > MAX_LOGO or not data.startswith(PNG):
        raise ValueError("Bitte ein PNG, höchstens 1,5 MB.")
    path = logo_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def clear_logo() -> None:
    logo_path().unlink(missing_ok=True)


def apply() -> str:
    script = Path("/usr/local/sbin/hibit-splash")
    if not sys.platform.startswith("linux") or not script.is_file():
        if has_custom_logo():
            return "Logo gespeichert. Der Boot-Splash wird beim nächsten Player-Update eingerichtet und erscheint danach beim Neustart."
        return "Standardlogo gespeichert. Der Boot-Splash wird beim nächsten Player-Update eingerichtet."
    result = subprocess.run(
        ["sudo", "-n", str(script)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    lines = [line.strip() for line in (result.stdout or result.stderr or "").splitlines() if line.strip()]
    message = lines[-1] if lines else ""
    if result.returncode != 0:
        raise RuntimeError(message or "Der Boot-Splash konnte nicht eingerichtet werden.")
    return message or "Boot-Splash gespeichert. Er erscheint beim nächsten Neustart."
