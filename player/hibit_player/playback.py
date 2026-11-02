"""Letzter Stand der Anzeige, gemeldet von der Player-Seite."""

from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_STATUS = {"phase": "empty", "name": "", "kind": "", "detail": ""}
_FRAME = b""
_FRAME_AT = 0.0
_WATCH_UNTIL = 0.0


def report(phase: str, name: str, kind: str, detail: str) -> None:
    with _LOCK:
        _STATUS["phase"] = phase
        _STATUS["name"] = name
        _STATUS["kind"] = kind
        _STATUS["detail"] = detail


def snapshot() -> dict:
    with _LOCK:
        state = dict(_STATUS)
    state["watching"] = watching()
    return state


def note_frame(data: bytes) -> None:
    global _FRAME, _FRAME_AT
    with _LOCK:
        _FRAME = data
        _FRAME_AT = time.time()


def frame_if_fresh(max_age: float = 6) -> bytes:
    with _LOCK:
        if not _FRAME or time.time() - _FRAME_AT > max_age:
            return b""
        if _STATUS.get("kind") == "web":
            return b""
        return _FRAME


def arm_watch(seconds: float = 8) -> None:
    global _WATCH_UNTIL
    with _LOCK:
        _WATCH_UNTIL = max(_WATCH_UNTIL, time.time() + seconds)


def watching() -> bool:
    with _LOCK:
        return time.time() < _WATCH_UNTIL
