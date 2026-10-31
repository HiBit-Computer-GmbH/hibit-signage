from __future__ import annotations

import socket
import subprocess
import sys


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
