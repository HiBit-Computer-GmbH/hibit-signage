"""Bild der laufenden Anzeige über den lokalen Chromium-Debugger."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.request


def _port() -> int:
    raw = os.environ.get("HIBIT_DEBUG_PORT", "9222")
    try:
        return int(raw)
    except ValueError:
        return 9222


def _page_socket() -> str:
    url = f"http://127.0.0.1:{_port()}/json/list"
    with urllib.request.urlopen(url, timeout=1.5) as response:
        tabs = json.loads(response.read().decode("utf-8", errors="replace"))
    for tab in tabs:
        if tab.get("type") != "page":
            continue
        if "/player" not in str(tab.get("url") or ""):
            continue
        socket = str(tab.get("webSocketDebuggerUrl") or "")
        if socket:
            return socket
    return ""


async def capture_jpeg() -> bytes:
    socket = ""
    try:
        socket = _page_socket()
    except Exception:
        return b""
    if not socket:
        return b""
    try:
        import websockets
    except ImportError:
        return b""
    try:
        async with websockets.connect(socket, open_timeout=2, max_size=8_000_000) as ws:
            for quality in (45, 25):
                await ws.send(
                    json.dumps(
                        {
                            "id": quality,
                            "method": "Page.captureScreenshot",
                            "params": {"format": "jpeg", "quality": quality, "fromSurface": True},
                        }
                    )
                )
                data = ""
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), 4))
                    if message.get("id") != quality:
                        continue
                    data = str((message.get("result") or {}).get("data") or "")
                    break
                if not data:
                    return b""
                raw = base64.b64decode(data)
                if len(raw) <= 1_200_000:
                    return raw
            return raw if len(raw) <= 1_800_000 else b""
    except Exception:
        return b""
