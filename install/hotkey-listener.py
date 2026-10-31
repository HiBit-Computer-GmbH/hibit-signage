#!/usr/bin/env python3
"""Wechselt mit Strg+Alt+S oder Strg+Alt+F2 auf tty2 und mit Strg+Alt+F1 zurück.

Cage startet zusätzlich mit -s, damit Strg+Alt+F-Tasten vom Kernel durchgereicht werden.
Dieser Hörer ist die Abkürzung, falls der Browser die Tasten schluckt.
"""

from __future__ import annotations

import ctypes
import glob
import os
import select
import subprocess
import time

EV_KEY = 1
KEY_LEFTCTRL, KEY_RIGHTCTRL = 29, 97
KEY_LEFTALT, KEY_RIGHTALT = 56, 100
KEY_S, KEY_D = 31, 32
KEY_F1, KEY_F2 = 59, 60
CTRL = {KEY_LEFTCTRL, KEY_RIGHTCTRL}
ALT = {KEY_LEFTALT, KEY_RIGHTALT}


class Timeval(ctypes.Structure):
    _fields_ = [("sec", ctypes.c_long), ("usec", ctypes.c_long)]


class InputEvent(ctypes.Structure):
    _fields_ = [
        ("time", Timeval),
        ("type", ctypes.c_uint16),
        ("code", ctypes.c_uint16),
        ("value", ctypes.c_int32),
    ]


SIZE = ctypes.sizeof(InputEvent)


def key_devices() -> list[str]:
    found = []
    for node in glob.glob("/dev/input/event*"):
        found.append(node)
    return found


def switch(tty: str) -> None:
    subprocess.run(["chvt", tty], check=False)
    print(f"HiBit Hotkey: tty{tty}", flush=True)


def main() -> None:
    pressed: set[int] = set()
    last = 0.0
    while True:
        paths = key_devices()
        if not paths:
            time.sleep(2)
            continue
        fds = []
        for path in paths:
            try:
                fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                continue
        if not fds:
            time.sleep(2)
            continue
        try:
            while True:
                readable, _, _ = select.select(fds, [], [], 2)
                if not readable and not key_devices():
                    break
                for fd in readable:
                    while True:
                        try:
                            raw = os.read(fd, SIZE)
                        except BlockingIOError:
                            break
                        except OSError:
                            break
                        if len(raw) < SIZE:
                            break
                        event = InputEvent.from_buffer_copy(raw)
                        if event.type != EV_KEY:
                            continue
                        if event.value == 1:
                            pressed.add(event.code)
                            ctrl = bool(pressed & CTRL)
                            alt = bool(pressed & ALT)
                            now = time.monotonic()
                            if ctrl and alt and now - last > 0.6:
                                if event.code in {KEY_S, KEY_F2}:
                                    last = now
                                    switch("2")
                                elif event.code in {KEY_F1, KEY_D}:
                                    last = now
                                    switch("1")
                        elif event.value == 0:
                            pressed.discard(event.code)
        finally:
            for fd in fds:
                os.close(fd)


if __name__ == "__main__":
    main()
