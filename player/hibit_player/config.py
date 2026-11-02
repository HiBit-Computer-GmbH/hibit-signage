from __future__ import annotations

import os
import sys
from pathlib import Path

VERSION_FALLBACK = "1.1.9"


def install_dir() -> Path:
    env = os.environ.get("HIBIT_INSTALL_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    env = os.environ.get("HIBIT_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".hibit-signage" / "player"


def media_dir() -> Path:
    return data_dir() / "media"


def credentials_dir() -> Path:
    return data_dir() / "credentials"


def mount_root() -> Path:
    if sys.platform.startswith("linux") and os.environ.get("HIBIT_DATA_DIR") == "/var/lib/hibit-signage":
        return Path("/mnt/hibit")
    if sys.platform.startswith("linux") and Path("/var/lib/hibit-signage").exists() and str(data_dir()) == "/var/lib/hibit-signage":
        return Path("/mnt/hibit")
    return data_dir() / "mounts"


def version() -> str:
    candidates = [
        install_dir() / "VERSION",
        Path(__file__).resolve().parents[2] / "VERSION",
    ]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return VERSION_FALLBACK


def port() -> int:
    return int(os.environ.get("HIBIT_PORT", "8080"))


def ensure_dirs() -> None:
    for path in (data_dir(), media_dir(), credentials_dir(), mount_root()):
        path.mkdir(parents=True, exist_ok=True)
