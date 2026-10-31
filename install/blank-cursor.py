#!/usr/bin/env python3
"""Unsichtbares Zeigerthema für den Kiosk.

libXcursor liest pro Bild nur Größe, Breite, Höhe, Hotspot, Verzögerung und
Pixel. Ein zusätzlicher Dateikopf lässt das Thema ungültig werden, dann zeichnet
Cage wieder den normalen Pfeil.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

IMAGE_TYPE = 0xFFFD0002
SIZES = (24, 32, 48, 64, 96)
NAMES = (
    "left_ptr",
    "arrow",
    "default",
    "hand",
    "hand1",
    "hand2",
    "pointer",
    "text",
    "xterm",
    "vertical-text",
    "crosshair",
    "watch",
    "wait",
    "progress",
    "help",
    "move",
    "fleur",
    "copy",
    "alias",
    "no-drop",
    "not-allowed",
    "grab",
    "grabbing",
    "cell",
    "context-menu",
    "all-scroll",
    "zoom-in",
    "zoom-out",
    "top_left_arrow",
    "left_ptr_watch",
    "x-cursor",
    "right_ptr",
    "plus",
    "target",
    "pirate",
    "sizing",
    "e-resize",
    "n-resize",
    "ne-resize",
    "nw-resize",
    "s-resize",
    "se-resize",
    "sw-resize",
    "w-resize",
    "ew-resize",
    "ns-resize",
    "nesw-resize",
    "nwse-resize",
    "col-resize",
    "row-resize",
    "sb_h_double_arrow",
    "sb_v_double_arrow",
    "top_side",
    "bottom_side",
    "left_side",
    "right_side",
    "top_left_corner",
    "top_right_corner",
    "bottom_left_corner",
    "bottom_right_corner",
)


def cursor_blob(sizes: tuple[int, ...] = SIZES) -> bytes:
    chunks = []
    for size in sizes:
        pixels = b"\x00" * (size * size * 4)
        chunks.append(struct.pack("<IIIIII", size, size, size, 0, 0, 0) + pixels)
    toc_pos = 16 + 12 * len(chunks)
    tocs = []
    position = toc_pos
    for size, chunk in zip(sizes, chunks):
        tocs.append(struct.pack("<III", IMAGE_TYPE, size, position))
        position += len(chunk)
    header = struct.pack("<4sIII", b"Xcur", 16, 0x00010000, len(chunks))
    return header + b"".join(tocs) + b"".join(chunks)


def theme_roots() -> list[Path]:
    home = Path(os.environ.get("HOME") or "/var/lib/hibit-signage")
    return [home / ".icons" / "hibit-blank", home / ".local" / "share" / "icons" / "hibit-blank"]


def system_cursor_names() -> set[str]:
    names = set(NAMES)
    icons = Path("/usr/share/icons")
    if not icons.is_dir():
        return names
    for folder in icons.glob("*/cursors"):
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            if entry.name and entry.name != "left_ptr":
                names.add(entry.name)
    return names


def install() -> None:
    blob = cursor_blob()
    names = system_cursor_names()
    for root in theme_roots():
        cursors = root / "cursors"
        cursors.mkdir(parents=True, exist_ok=True)
        (cursors / "left_ptr").write_bytes(blob)
        for name in names:
            if name == "left_ptr":
                continue
            (cursors / name).write_bytes(blob)
        (root / "index.theme").write_text("[Icon Theme]\nName=hibit-blank\n", encoding="utf-8")


if __name__ == "__main__":
    install()
