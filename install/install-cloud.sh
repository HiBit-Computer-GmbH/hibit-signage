#!/bin/bash
# HiBit Signage Cloud auf einem Linux-Server installieren.
# Beispiel:
#   sudo ./install/install-cloud.sh --admin-email admin@example.com --behind-proxy
set -euo pipefail

REPO=""
BRANCH="main"
PORT="8090"
ADMIN_EMAIL=""
ADMIN_PASSWORD=""
ADMIN_NAME="Cloud-Admin"
BEHIND_PROXY=0
TARGET="/opt/hibit-signage"
DATA_DIR="/var/lib/hibit-cloud"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-main}"; shift 2 ;;
    --port) PORT="${2:-8090}"; shift 2 ;;
    --admin-email) ADMIN_EMAIL="${2:-}"; shift 2 ;;
    --admin-password) ADMIN_PASSWORD="${2:-}"; shift 2 ;;
    --admin-name) ADMIN_NAME="${2:-}"; shift 2 ;;
    --behind-proxy) BEHIND_PROXY=1; shift ;;
    *) echo "Unbekanntes Argument: $1" >&2; exit 1 ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Bitte mit sudo ausführen." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "Dieses Skript erwartet Debian oder ein vergleichbares System mit apt." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip git ca-certificates rsync sudo

if ! id hibit >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$DATA_DIR" --shell /usr/sbin/nologin hibit
fi

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$TARGET"
if [[ -n "$REPO" && ! -d "$TARGET/.git" ]]; then
  git clone --branch "$BRANCH" "$REPO" "$TARGET"
elif [[ "$SOURCE" != "$TARGET" ]]; then
  if [[ ! -f "$SOURCE/cloud/requirements.txt" ]]; then
    echo "Repository nicht gefunden. Bitte klonen oder --repo angeben." >&2
    exit 1
  fi
  rsync -a --delete --exclude '.venv' --exclude 'data' "$SOURCE/" "$TARGET/"
fi

if [[ ! -d "$TARGET/.venv" ]]; then
  python3 -m venv "$TARGET/.venv"
fi
"$TARGET/.venv/bin/pip" install --upgrade pip
"$TARGET/.venv/bin/pip" install -r "$TARGET/cloud/requirements.txt"

mkdir -p "$DATA_DIR" /etc/hibit /usr/local/sbin
chown -R hibit:hibit "$DATA_DIR"
chmod 750 "$DATA_DIR"

install -m 755 "$TARGET/install/hibit-update-cloud" /usr/local/sbin/hibit-update-cloud
cat >/etc/sudoers.d/hibit-cloud <<'EOF'
hibit ALL=(root) NOPASSWD: /usr/local/sbin/hibit-update-cloud
EOF
chmod 440 /etc/sudoers.d/hibit-cloud

if [[ -z "$ADMIN_PASSWORD" ]]; then
  ADMIN_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(14))')"
fi
if [[ -z "$ADMIN_EMAIL" ]]; then
  ADMIN_EMAIL="admin@hibit.local"
fi

INIT_OUT="$(sudo -u hibit env \
  PYTHONPATH="$TARGET/cloud" \
  HIBIT_DATA_DIR="$DATA_DIR" \
  HIBIT_INSTALL_DIR="$TARGET" \
  HIBIT_PORT="$PORT" \
  "$TARGET/.venv/bin/python" -m hibit_cloud --init-only \
    --admin-email "$ADMIN_EMAIL" \
    --admin-password "$ADMIN_PASSWORD" \
    --admin-name "$ADMIN_NAME")"

if [[ "$BEHIND_PROXY" -eq 0 && -f /etc/hibit/cloud.env ]] && grep -q '^HIBIT_TRUST_PROXY=1$' /etc/hibit/cloud.env; then
  BEHIND_PROXY=1
fi

cat >/etc/hibit/cloud.env <<EOF
HIBIT_DATA_DIR=${DATA_DIR}
HIBIT_INSTALL_DIR=${TARGET}
HIBIT_PORT=${PORT}
EOF
if [[ "$BEHIND_PROXY" -eq 1 ]]; then
  printf 'HIBIT_TRUST_PROXY=1\nHIBIT_COOKIE_SECURE=1\n' >>/etc/hibit/cloud.env
fi
chmod 640 /etc/hibit/cloud.env
chown root:hibit /etc/hibit/cloud.env

install -m 644 "$TARGET/install/hibit-cloud.service" /etc/systemd/system/hibit-cloud.service
systemctl daemon-reload
systemctl enable hibit-cloud.service
systemctl restart hibit-cloud.service

echo
echo "HiBit Signage Cloud lauscht auf Port ${PORT}."
if [[ "$INIT_OUT" == *HIBIT_ADMIN_EXISTS* ]]; then
  echo "Das bestehende Admin-Konto wurde beibehalten."
else
  echo "E-Mail:    ${ADMIN_EMAIL}"
  echo "Passwort:  ${ADMIN_PASSWORD}"
  echo "Gespeichert in ${DATA_DIR}/initial-password.txt"
fi
if [[ "$BEHIND_PROXY" -eq 1 ]]; then
  echo "Proxy-Modus ist aktiv. Im Nginx Proxy Manager den Host auf Port ${PORT} zeigen,"
  echo "Websockets einschalten und in der Custom-Config client_max_body_size 4096m setzen."
else
  echo "Hinter einem Reverse Proxy die Installation mit --behind-proxy wiederholen."
fi
