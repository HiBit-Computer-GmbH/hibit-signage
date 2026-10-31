from __future__ import annotations

import os
from pathlib import Path

VERSION_FALLBACK = "1.1.5"


def install_dir() -> Path:
    env = os.environ.get("HIBIT_INSTALL_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    env = os.environ.get("HIBIT_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".hibit-signage" / "cloud"


def files_dir() -> Path:
    return data_dir() / "files"


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
    return int(os.environ.get("HIBIT_PORT", "8090"))


def ensure_dirs() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    files_dir().mkdir(parents=True, exist_ok=True)
