from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

IPV4 = re.compile(r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$")
HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
UUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def validate(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Die Angaben sind unvollständig.")
    hostname = str(data.get("hostname") or "").strip()
    if hostname and not HOST.match(hostname):
        raise ValueError("Der Hostname darf Buchstaben, Ziffern und Bindestriche enthalten.")
    connection = str(data.get("connection") or "").strip()
    if not UUID.match(connection):
        raise ValueError("Bitte eine vorhandene Verbindung wählen.")
    method = str(data.get("method") or "auto").strip()
    if method not in {"auto", "manual"}:
        raise ValueError("Bitte automatisch oder eine feste Adresse wählen.")
    address = str(data.get("address") or "").strip()
    gateway = str(data.get("gateway") or "").strip()
    prefix = int(data.get("prefix") or 24)
    if method == "manual":
        if not IPV4.match(address):
            raise ValueError("Die Adresse muss eine IPv4-Adresse sein.")
        if prefix < 1 or prefix > 32:
            raise ValueError("Das Präfix liegt zwischen 1 und 32.")
        if gateway and not IPV4.match(gateway):
            raise ValueError("Das Gateway muss eine IPv4-Adresse sein.")
    dns = _dns(data.get("dns"))
    ssid = str(data.get("ssid") or "").strip()
    if ssid and (len(ssid) > 32 or any(ord(char) < 32 for char in ssid)):
        raise ValueError("Der WLAN-Name ist ungültig.")
    psk = str(data.get("psk") or "")
    if psk and (len(psk) < 8 or len(psk) > 63 or any(ord(char) < 32 for char in psk)):
        raise ValueError("Das WLAN-Passwort braucht 8 bis 63 Zeichen.")
    return {
        "hostname": hostname,
        "connection": connection,
        "method": method,
        "address": address,
        "prefix": prefix,
        "gateway": gateway,
        "dns": dns,
        "ssid": ssid,
        "psk": psk,
    }


def _dns(value) -> list[str]:
    if isinstance(value, list):
        parts = [str(item).strip() for item in value]
    else:
        parts = re.split(r"[\s,;]+", str(value or ""))
    found = [item for item in parts if item]
    if len(found) > 3:
        raise ValueError("Höchstens drei DNS-Server.")
    for item in found:
        if not IPV4.match(item):
            raise ValueError("DNS muss eine IPv4-Adresse sein.")
    return found


def snapshot() -> dict:
    if not shutil.which("nmcli"):
        raise RuntimeError("NetworkManager fehlt auf diesem Pi.")
    raw = _nm("-g", "NAME,UUID,TYPE,DEVICE", "connection", "show")
    current = _current_addrs()
    connections = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = _split(line)
        if len(parts) < 4:
            continue
        name, uuid, kind, device = parts[0], parts[1], parts[2], parts[3]
        if kind == "802-3-ethernet":
            simple = "ethernet"
        elif kind == "802-11-wireless":
            simple = "wifi"
        else:
            continue
        if device in {"", "--"}:
            device = ""
        info = _connection(uuid)
        address, prefix = _cidr(info["addresses"])
        live = current.get(device, "")
        connections.append({
            "id": uuid,
            "name": name,
            "kind": simple,
            "device": device,
            "active": bool(device),
            "method": "manual" if info["method"] == "manual" else "auto",
            "address": address,
            "prefix": prefix,
            "gateway": info["gateway"],
            "dns": info["dns"],
            "ssid": info["ssid"],
            "current": live,
        })
    return {"hostname": _hostname(), "connections": connections}


def wifi_scan() -> dict:
    if not shutil.which("nmcli"):
        raise RuntimeError("NetworkManager fehlt auf diesem Pi.")
    raw = _nm("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes", timeout=25)
    best: dict[str, dict] = {}
    for line in raw.splitlines():
        parts = _split(line)
        if len(parts) < 2:
            continue
        ssid = parts[0].strip()
        if not ssid:
            continue
        try:
            signal = int(parts[1] or "0")
        except ValueError:
            signal = 0
        security = parts[2] if len(parts) > 2 else ""
        current = best.get(ssid)
        if current is None or signal > current["signal"]:
            best[ssid] = {"ssid": ssid, "signal": signal, "security": security}
    networks = sorted(best.values(), key=lambda item: (-item["signal"], item["ssid"].lower()))
    return {"networks": networks[:30]}


def apply(data: dict) -> str:
    clean = validate(data)
    if not shutil.which("nmcli"):
        raise RuntimeError("NetworkManager fehlt auf diesem Pi.")
    known = {item["id"]: item for item in snapshot()["connections"]}
    current = known.get(clean["connection"])
    if not current:
        raise RuntimeError("Diese Verbindung gibt es nicht.")
    if clean["hostname"]:
        _check(["hostnamectl", "set-hostname", clean["hostname"]])
    uuid = clean["connection"]
    if clean["method"] == "auto":
        _nm(
            "connection", "modify", uuid,
            "ipv4.method", "auto",
            "ipv4.addresses", "",
            "ipv4.gateway", "",
            "ipv4.dns", "",
        )
    else:
        _nm(
            "connection", "modify", uuid,
            "ipv4.method", "manual",
            "ipv4.addresses", f"{clean['address']}/{clean['prefix']}",
            "ipv4.gateway", clean["gateway"],
            "ipv4.dns", ",".join(clean["dns"]),
        )
    if current["kind"] == "wifi" and clean["ssid"]:
        args = ["connection", "modify", uuid, "802-11-wireless.ssid", clean["ssid"]]
        if clean["psk"]:
            args.extend(["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", clean["psk"]])
        _nm(*args)
    _defer_up(uuid)
    return "Gespeichert. Die Verbindung wird neu aufgebaut. Die Adresse kann sich ändern."


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if action == "show":
            print(json.dumps(snapshot()), flush=True)
        elif action == "scan":
            print(json.dumps(wifi_scan()), flush=True)
        elif action == "apply":
            payload = json.loads(sys.stdin.read() or "{}")
            print(json.dumps({"ok": True, "message": apply(payload)}), flush=True)
        else:
            raise RuntimeError("Unbekannte Aktion.")
    except (ValueError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        sys.stderr.write(str(exc).strip() + "\n")
        sys.exit(1)


def _hostname() -> str:
    try:
        proc = subprocess.run(["hostnamectl", "--static"], capture_output=True, text=True, timeout=4)
        name = (proc.stdout or "").strip()
        if proc.returncode == 0 and name:
            return name
    except (OSError, subprocess.SubprocessError):
        pass
    return socket.gethostname() or "hibit-signage"


def _connection(uuid: str) -> dict:
    text = _nm(
        "-g",
        "ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns,802-11-wireless.ssid",
        "connection",
        "show",
        uuid,
    ).strip()
    lines = text.splitlines()
    if len(lines) >= 5:
        fields = lines[:4] + [":".join(lines[4:])]
    else:
        fields = _split(text)
    while len(fields) < 5:
        fields.append("")
    method, addresses, gateway, dns, ssid = fields[:5]
    if len(fields) > 5:
        ssid = ":".join(fields[4:])
    return {
        "method": method.strip(),
        "addresses": addresses.strip(),
        "gateway": "" if gateway.strip() in {"", "--"} else gateway.strip(),
        "dns": _dns_loose(dns),
        "ssid": "" if ssid.strip() in {"", "--"} else ssid.strip(),
    }


def _dns_loose(value: str) -> list[str]:
    found = []
    for part in re.split(r"[\s,;]+", value or ""):
        part = part.strip()
        if IPV4.match(part) and part not in found:
            found.append(part)
    return found[:3]


def _cidr(value: str) -> tuple[str, int]:
    raw = (value or "").split(",")[0].strip()
    if not raw or raw == "--":
        return "", 24
    if "/" not in raw:
        return (raw if IPV4.match(raw) else ""), 24
    address, prefix = raw.split("/", 1)
    try:
        number = int(prefix)
    except ValueError:
        number = 24
    if not IPV4.match(address):
        return "", 24
    return address, number


def _current_addrs() -> dict[str, str]:
    found: dict[str, str] = {}
    try:
        proc = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return found
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        device = parts[1]
        if device not in found:
            found[device] = parts[3]
    return found


def _split(line: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    escaped = False
    for char in line.rstrip("\n"):
        if escaped:
            buf.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append("".join(buf))
    return parts


def _nm(*args: str, timeout: int = 12) -> str:
    _check(["nmcli", *args], timeout=timeout)
    return _last_stdout


_last_stdout = ""


def _check(args: list[str], timeout: int = 12) -> None:
    global _last_stdout
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    _last_stdout = proc.stdout or ""
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "NetworkManager hat die Änderung abgelehnt.").strip()
        raise RuntimeError(detail.splitlines()[-1][:300])


def _defer_up(uuid: str) -> None:
    pid = os.fork()
    if pid != 0:
        return
    os.setsid()
    try:
        time.sleep(1.2)
        subprocess.run(
            ["nmcli", "connection", "up", uuid],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    finally:
        os._exit(0)
