from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from hibit_player.config import install_dir
from hibit_player.netcfg import validate


def local_ips() -> list[str]:
    found: list[str] = []

    def add(value: str) -> None:
        value = (value or "").strip()
        if not value or value.startswith("127.") or value == "::1":
            return
        if value not in found:
            found.append(value)

    if sys.platform.startswith("linux"):
        try:
            out = subprocess.check_output(["ip", "-4", "-o", "addr", "show", "scope", "global"], text=True, timeout=3)
            for line in out.splitlines():
                parts = line.split()
                if "inet" in parts:
                    addr = parts[parts.index("inet") + 1].split("/", 1)[0]
                    add(addr)
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            pass
        try:
            out = subprocess.check_output(["hostname", "-I"], text=True, timeout=3)
            for part in out.split():
                if ":" not in part:
                    add(part)
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        sock.connect(("8.8.8.8", 80))
        add(sock.getsockname()[0])
        sock.close()
    except OSError:
        pass

    if not found:
        try:
            add(socket.gethostbyname(socket.gethostname()))
        except OSError:
            pass
    return found


def hostname() -> str:
    return socket.gethostname() or "hibit-signage"


def _script() -> Path | None:
    installed = Path("/usr/local/sbin/hibit-network")
    if installed.exists():
        return installed
    local = install_dir() / "install" / "hibit-network"
    if local.exists():
        return local
    return None


def _unavailable(detail: str) -> dict:
    return {"available": False, "detail": detail, "hostname": hostname(), "connections": []}


def _call(action: str, payload: dict | None = None, timeout: int = 20) -> dict:
    script = _script()
    if not sys.platform.startswith("linux") or script is None:
        raise RuntimeError("Netzwerk lässt sich auf diesem System nicht einstellen.")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        command = [sys.executable, str(script), action]
    else:
        command = ["sudo", "-n", str(script), action]
    try:
        proc = subprocess.run(
            command,
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Die Netzwerkkonfiguration ist nicht erreichbar.") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "Netzwerk konnte nicht geändert werden.").strip()
        lowered = detail.lower()
        if "password is required" in lowered or "not allowed" in lowered or "a terminal is required" in lowered:
            detail = "Die Netzwerk-Berechtigung fehlt. Bitte den Player einmal updaten."
        raise RuntimeError(detail.splitlines()[-1][:400])
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Die Netzwerkkonfiguration hat keine Antwort geliefert.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Die Netzwerkkonfiguration hat keine Antwort geliefert.")
    return data


def network_status() -> dict:
    try:
        data = _call("show", timeout=15)
    except RuntimeError as exc:
        return _unavailable(str(exc))
    data["available"] = True
    data.setdefault("hostname", hostname())
    data.setdefault("connections", [])
    return data


def network_scan() -> dict:
    data = _call("scan", timeout=30)
    networks = data.get("networks")
    if not isinstance(networks, list):
        raise RuntimeError("Die Suche hat keine Netze geliefert.")
    return {"networks": networks}


def network_apply(data: dict) -> str:
    clean = validate(data)
    result = _call("apply", clean, timeout=25)
    return str(result.get("message") or "Gespeichert. Die Verbindung wird neu aufgebaut.")
