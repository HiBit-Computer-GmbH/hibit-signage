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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/blank-cursor.py"
export XCURSOR_THEME=hibit-blank
export XCURSOR_SIZE=24
export XCURSOR_PATH="${HOME}/.icons:${HOME}/.local/share/icons"

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
