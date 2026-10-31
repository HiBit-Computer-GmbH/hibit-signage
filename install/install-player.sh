#!/bin/bash
# HiBit Signage auf Raspberry Pi OS Lite installieren.
# Beispiel:
#   sudo ./install/install-player.sh --cloud-url https://signage.example.com --device-key HB-XXXX-XXXX-XXXX-XXXX
set -euo pipefail

CLOUD_URL=""
DEVICE_KEY=""
SET_CLOUD=0
SET_KEY=0
REPO=""
BRANCH="main"
PORT="8080"
ADMIN_PASSWORD=""
TARGET="/opt/hibit-signage"
DATA_DIR="/var/lib/hibit-signage"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cloud-url) CLOUD_URL="${2:-}"; SET_CLOUD=1; shift 2 ;;
    --device-key) DEVICE_KEY="${2:-}"; SET_KEY=1; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-main}"; shift 2 ;;
    --port) PORT="${2:-8080}"; shift 2 ;;
    --admin-password) ADMIN_PASSWORD="${2:-}"; shift 2 ;;
    *) echo "Unbekanntes Argument: $1" >&2; exit 1 ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Bitte mit sudo ausführen." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "Dieses Skript erwartet Raspberry Pi OS oder Debian mit apt." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  python3 python3-venv python3-pip \
  git curl ca-certificates rsync \
  cage \
  cifs-utils nfs-common \
  sudo

if ! apt-get install -y chromium; then
  apt-get install -y chromium-browser
fi

CREATED_HIBIT=0
if ! id hibit >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$DATA_DIR" --shell /bin/bash hibit
  CREATED_HIBIT=1
  passwd -l hibit >/dev/null 2>&1 || true
elif passwd -S hibit 2>/dev/null | grep -q ' L '; then
  # Ein bestehendes Konto, zum Beispiel aus dem Raspberry Pi Imager, wurde früher
  # mit passwd -l gesperrt. Die Sperre nur aufheben, das Passwort bleibt das alte.
  passwd -u hibit >/dev/null 2>&1 || true
fi
for group in video render input tty; do
  if getent group "$group" >/dev/null; then
    usermod -aG "$group" hibit || true
  fi
done

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$TARGET"
if [[ -n "$REPO" && ! -d "$TARGET/.git" ]]; then
  git clone --branch "$BRANCH" "$REPO" "$TARGET"
elif [[ "$SOURCE" != "$TARGET" ]]; then
  if [[ ! -f "$SOURCE/player/requirements.txt" ]]; then
    echo "Repository nicht gefunden. Bitte klonen oder --repo angeben." >&2
    exit 1
  fi
  rsync -a --delete --exclude '.venv' --exclude 'data' "$SOURCE/" "$TARGET/"
fi

python3 -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/pip" install --upgrade pip
"$TARGET/.venv/bin/pip" install -r "$TARGET/player/requirements.txt"

mkdir -p "$DATA_DIR" /mnt/hibit /etc/hibit /usr/local/sbin
chown hibit:hibit "$DATA_DIR"
chmod 750 "$DATA_DIR"

install -m 755 "$TARGET/install/hibit-mount" /usr/local/sbin/hibit-mount
install -m 755 "$TARGET/install/hibit-update-player" /usr/local/sbin/hibit-update-player
install -m 755 "$TARGET/install/hibit-power" /usr/local/sbin/hibit-power
python3 "$TARGET/install/blank-cursor.py" --system || true
SYSTEMCTL="$(command -v systemctl)"
cat >/etc/sudoers.d/hibit-signage <<EOF
hibit ALL=(root) NOPASSWD: /usr/local/sbin/hibit-mount, /usr/local/sbin/hibit-update-player, /usr/local/sbin/hibit-power, ${SYSTEMCTL} restart hibit-kiosk
EOF
chmod 440 /etc/sudoers.d/hibit-signage

for policy_dir in /etc/chromium/policies/managed /etc/chromium-browser/policies/managed; do
  mkdir -p "$policy_dir"
  printf '%s\n' '{"TranslateEnabled":false}' > "$policy_dir/hibit-signage.json"
  chmod 644 "$policy_dir/hibit-signage.json"
done

if [[ -z "$ADMIN_PASSWORD" ]]; then
  ADMIN_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
fi

INIT_ARGS=(--init-only --admin-password "$ADMIN_PASSWORD")
if [[ "$SET_CLOUD" -eq 1 ]]; then
  INIT_ARGS+=(--cloud-url "$CLOUD_URL")
fi
if [[ "$SET_KEY" -eq 1 ]]; then
  INIT_ARGS+=(--device-key "$DEVICE_KEY")
fi
INIT_OUT="$(sudo -u hibit env \
  PYTHONPATH="$TARGET/player" \
  HIBIT_DATA_DIR="$DATA_DIR" \
  HIBIT_INSTALL_DIR="$TARGET" \
  HIBIT_PORT="$PORT" \
  "$TARGET/.venv/bin/python" -m hibit_player "${INIT_ARGS[@]}")"

cat >/etc/hibit/player.env <<EOF
HIBIT_DATA_DIR=${DATA_DIR}
HIBIT_INSTALL_DIR=${TARGET}
HIBIT_PORT=${PORT}
EOF
chmod 640 /etc/hibit/player.env
chown root:hibit /etc/hibit/player.env

install -m 644 "$TARGET/install/hibit-signage.service" /etc/systemd/system/hibit-signage.service
install -m 644 "$TARGET/install/hibit-kiosk.service" /etc/systemd/system/hibit-kiosk.service
install -m 644 "$TARGET/install/hibit-hotkey.service" /etc/systemd/system/hibit-hotkey.service
chmod 755 "$TARGET/install/kiosk-start.sh" "$TARGET/install/hotkey-listener.py"

ISSUE_MARK="HiBit Signage: Strg+Alt+F1 kehrt zur Anzeige zurück."
if ! grep -qF "$ISSUE_MARK" /etc/issue 2>/dev/null; then
  printf '\n%s\n' "$ISSUE_MARK" >> /etc/issue
fi

systemctl daemon-reload
systemctl disable getty@tty1.service || true
systemctl enable getty@tty2.service || true
systemctl enable hibit-signage.service hibit-kiosk.service hibit-hotkey.service
systemctl restart hibit-signage.service
systemctl restart hibit-hotkey.service
systemctl restart hibit-kiosk.service || true

echo
echo "HiBit Signage ist installiert."
echo "Anzeige: tty1, Shell: Strg+Alt+S oder Strg+Alt+F2, zurück mit Strg+Alt+F1"
if [[ "$INIT_OUT" == *HIBIT_ADMIN_EXISTS* ]]; then
  echo "Das bestehende Admin-Konto wurde beibehalten."
else
  echo "Webkonsole-Benutzer: admin"
  echo "Webkonsole-Passwort: ${ADMIN_PASSWORD}"
  echo "Das Passwort liegt auch in ${DATA_DIR}/initial-password.txt und sollte danach gelöscht werden."
fi
if [[ -n "$CLOUD_URL" ]]; then
  echo "Cloud: ${CLOUD_URL} (Gerät wartet dort auf den Adopt)"
fi
echo "Lokale Konsole: http://<IP-des-Pi>:${PORT}/"
