#!/bin/bash
set -euo pipefail
PORT="${HIBIT_PORT:-8080}"
URL="http://127.0.0.1:${PORT}/player/"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/hibit}"
export LANG="${LANG:-de_DE.UTF-8}"
export LANGUAGE="${LANGUAGE:-de_DE:de}"

until curl -sf --max-time 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null; do
  sleep 1
done

if command -v chromium >/dev/null 2>&1; then
  CHROME="$(command -v chromium)"
elif command -v chromium-browser >/dev/null 2>&1; then
  CHROME="$(command -v chromium-browser)"
else
  echo "Chromium ist nicht installiert." >&2
  exit 1
fi

(
  fails=0
  while true; do
    sleep 20
    if curl -sf --max-time 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
      fails=0
    else
      fails=$((fails + 1))
    fi
    if [[ "$fails" -ge 3 ]]; then
      pkill -f "${CHROME}.*${URL}" || true
      fails=0
    fi
  done
) &

python3 - <<'PY'
import os
import struct
from pathlib import Path

home = Path(os.environ.get("HOME") or "/var/lib/hibit-signage")
root = home / ".local" / "share" / "icons" / "hibit-blank"
cursors = root / "cursors"
cursors.mkdir(parents=True, exist_ok=True)
size = 24
image_type = 0xFFFD0002
chunk = struct.pack("<9I", 36, image_type, size, 1, size, size, 0, 0, 0) + (b"\x00" * (size * size * 4))
blob = struct.pack("<4sIII", b"Xcur", 16, 0x00010000, 1) + struct.pack("<III", image_type, size, 28) + chunk
(cursors / "left_ptr").write_bytes(blob)
for name in (
    "arrow", "default", "hand", "hand1", "hand2", "pointer", "text", "xterm",
    "crosshair", "watch", "wait", "progress", "help", "move", "copy", "alias",
    "no-drop", "not-allowed", "grab", "grabbing", "cell", "vertical-text",
    "context-menu", "all-scroll", "top_left_arrow", "left_ptr_watch", "x-cursor",
    "right_ptr", "plus",
):
    link = cursors / name
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to("left_ptr")
(root / "index.theme").write_text("[Icon Theme]\nName=hibit-blank\n", encoding="utf-8")
PY
export XCURSOR_THEME=hibit-blank
export XCURSOR_SIZE=24

exec cage -s -- "$CHROME" \
  --kiosk \
  --start-fullscreen \
  --no-first-run \
  --disable-infobars \
  --disable-translate \
  --disable-features=Translate,TranslateUI \
  --lang=de \
  --accept-lang=de-DE,de \
  --autoplay-policy=no-user-gesture-required \
  --noerrdialogs \
  --disable-session-crashed-bubble \
  --disable-component-update \
  --check-for-update-interval=31536000 \
  --overscroll-history-navigation=0 \
  --password-store=basic \
  --ozone-platform=wayland \
  "$URL"
