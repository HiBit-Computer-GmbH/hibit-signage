#!/bin/bash
# Der Dienst darf beim Neustart nur seinen Hauptprozess beenden.
# Sonst stirbt dieses Update-Skript mit und die Oberfläche meldet einen Fehler,
# obwohl der neue Stand schon gezogen ist.
if [[ "$(id -u)" -ne 0 ]]; then
  exit 0
fi
changed=0
note=$'[Service]\nKillMode=process\n'
for unit in hibit-cloud.service hibit-signage.service; do
  if [[ ! -f "/etc/systemd/system/${unit}" ]]; then
    continue
  fi
  dir="/etc/systemd/system/${unit}.d"
  mkdir -p "$dir"
  target="${dir}/hibit-update.conf"
  current=""
  if [[ -f "$target" ]]; then
    current="$(cat "$target")"
  fi
  if [[ "$current" != "$note" ]]; then
    printf '%s' "$note" > "$target"
    changed=1
  fi
done
if [[ "$changed" -eq 1 ]]; then
  if [[ -x /usr/bin/systemctl ]]; then
    /usr/bin/systemctl daemon-reload || true
  else
    systemctl daemon-reload || true
  fi
fi
