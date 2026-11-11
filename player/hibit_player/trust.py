"""Zusätzliche Aussteller-Zertifikate für Webseiten hinter einer Firewall."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
from pathlib import Path

from hibit_player.config import data_dir

_LOCK = threading.Lock()
_BLOCK = re.compile(
    r"-----BEGIN CERTIFICATE-----\s*[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----",
    re.S,
)
_HOST = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])$"
)
_ID = re.compile(r"^[a-f0-9]{16}$")
_MAX = 12
_CTX: ssl.SSLContext | None = None
_STAMP: tuple | None = None


def trust_dir() -> Path:
    return data_dir() / "trust"


def list_certs() -> dict:
    records = []
    folder = trust_dir()
    if folder.is_dir():
        for path in sorted(folder.glob("*.pem")):
            if not _ID.match(path.stem):
                continue
            try:
                record = describe_pem(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            record.pop("pem", None)
            records.append(record)
    return {"available": True, "certs": records}


def add_pem(pem: str) -> dict:
    blocks = _blocks(pem)
    if not blocks:
        raise ValueError("Die Datei enthält kein Zertifikat. Erwartet wird eine PEM-Datei mit BEGIN CERTIFICATE.")
    if len(blocks) > 3:
        raise ValueError("Bitte höchstens drei Zertifikate auf einmal.")
    saved = []
    with _LOCK:
        folder = trust_dir()
        folder.mkdir(parents=True, exist_ok=True)
        existing = {path.stem for path in folder.glob("*.pem")}
        for block in blocks:
            record = describe_pem(block)
            if record["id"] not in existing and len(existing) >= _MAX:
                raise ValueError("Es sind schon zwölf Zertifikate gespeichert.")
            path = folder / f"{record['id']}.pem"
            path.write_text(record["pem"], encoding="utf-8")
            existing.add(record["id"])
            record.pop("pem", None)
            saved.append(record)
        _drop_context()
    system = _sync_system()
    message = "Zertifikat gespeichert. Der Player vertraut diesem Aussteller jetzt."
    if system:
        message = "Zertifikat gespeichert. Der Player und die Systemupdates vertrauen diesem Aussteller jetzt."
    return {"certs": saved, "message": message}


def remove_cert(cert_id: str) -> str:
    if not _ID.match(cert_id or ""):
        raise ValueError("Unbekanntes Zertifikat.")
    with _LOCK:
        path = trust_dir() / f"{cert_id}.pem"
        if not path.is_file():
            raise ValueError("Dieses Zertifikat ist nicht gespeichert.")
        path.unlink()
        _drop_context()
    _sync_system()
    return "Zertifikat entfernt."


def inspect_host(host: str) -> dict:
    clean = _host(host)
    if shutil.which("openssl") is None:
        raise RuntimeError("Das Auslesen geht auf diesem System nicht. Bitte die PEM-Datei hochladen.")
    try:
        proc = subprocess.run(
            ["openssl", "s_client", "-showcerts", "-servername", clean, "-connect", f"{clean}:443"],
            input="",
            capture_output=True,
            text=True,
            timeout=12,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Die Adresse hat nicht geantwortet.") from exc
    blocks = _blocks((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if not blocks:
        raise RuntimeError("Die Adresse hat kein Zertifikat geschickt.")
    described = [describe_pem(block) for block in blocks]
    leaf = described[0]
    signer = next((item for item in described[1:] if item["subject"] == leaf["issuer"]), None)
    if signer is None and len(described) > 1:
        signer = described[-1]
    if signer is None or signer["id"] == leaf["id"]:
        raise RuntimeError(
            "Der Server schickt nur sein eigenes Zertifikat, nicht das der Firewall. "
            "Bitte die Zertifikatsdatei der Firewall hochladen."
        )
    already = (trust_dir() / f"{signer['id']}.pem").is_file()
    result = {
        "host": clean,
        "subject": signer["subject"],
        "issuer": signer["issuer"],
        "not_after": signer["not_after"],
        "already": already,
        "detail": (
            "Dieses Zertifikat ist schon gespeichert."
            if already
            else f"{clean} wird von {signer['subject']} signiert. Nach dem Speichern vertraut der Player den Seiten dieses Ausstellers."
        ),
    }
    if not already:
        result["pem"] = signer["pem"]
    return result


def client_context() -> ssl.SSLContext:
    global _CTX, _STAMP
    stamp = _stamp()
    with _LOCK:
        if _CTX is not None and stamp == _STAMP:
            return _CTX
        ctx = ssl.create_default_context()
        bundle = _bundle()
        if bundle:
            ctx.load_verify_locations(cadata=bundle)
        _CTX = ctx
        _STAMP = stamp
        return ctx


def describe_pem(pem: str) -> dict:
    blocks = _blocks(pem)
    if len(blocks) != 1:
        raise ValueError("Das Zertifikat lässt sich nicht lesen.")
    text = blocks[0]
    try:
        der = ssl.PEM_cert_to_DER_cert(text)
    except ssl.SSLError as exc:
        raise ValueError("Das Zertifikat lässt sich nicht lesen.") from exc
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        ctx.load_verify_locations(cadata=text)
    except ssl.SSLError as exc:
        raise ValueError("Das Zertifikat lässt sich nicht lesen.") from exc
    info = None
    infos = ctx.get_ca_certs()
    raws = ctx.get_ca_certs(binary_form=True)
    for item, raw in zip(infos, raws):
        if raw == der:
            info = item
            break
    if not isinstance(info, dict):
        raise ValueError("Das Zertifikat lässt sich nicht lesen.")
    digest = hashlib.sha256(der).hexdigest()
    return {
        "id": digest[:16],
        "subject": _name(info.get("subject")),
        "issuer": _name(info.get("issuer")),
        "not_after": _when(str(info.get("notAfter") or "")),
        "pem": text if text.endswith("\n") else text + "\n",
    }


def _blocks(pem: str) -> list[str]:
    found = []
    for match in _BLOCK.finditer(pem or ""):
        raw = match.group(0)
        body = raw.split("-----BEGIN CERTIFICATE-----", 1)[-1].split("-----END CERTIFICATE-----", 1)[0]
        body = re.sub(r"\s+", "", body)
        if not body or not re.fullmatch(r"[A-Za-z0-9+/=]+", body):
            continue
        lines = [body[index:index + 64] for index in range(0, len(body), 64)]
        found.append("-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----\n")
    return found


def _host(value: str) -> str:
    raw = (value or "").strip()
    raw = re.sub(r"^https?://", "", raw, flags=re.I)
    raw = raw.split("/")[0].split("?")[0].split("@")[-1]
    if raw.startswith("["):
        raise ValueError("Bitte einen Hostnamen angeben.")
    host = raw.split(":")[0].strip().rstrip(".")
    if not host or len(host) > 253 or not _HOST.match(host):
        raise ValueError("Bitte einen Hostnamen angeben.")
    if host.replace(".", "").isdigit():
        octets = host.split(".")
        if len(octets) != 4 or any(not part.isdigit() or int(part) > 255 for part in octets):
            raise ValueError("Bitte einen Hostnamen angeben.")
    return host


def _name(parts) -> str:
    flat: dict[str, str] = {}
    for rdn in parts or ():
        for key, value in rdn:
            flat.setdefault(str(key), str(value))
    common = flat.get("commonName") or flat.get("organizationName") or "Zertifikat"
    org = flat.get("organizationName") or ""
    if org and org != common:
        return f"{common} ({org})"
    return common


def _when(value: str) -> str:
    return " ".join(value.replace("GMT", "").split())


def _bundle() -> str:
    folder = trust_dir()
    if not folder.is_dir():
        return ""
    chunks = []
    for path in sorted(folder.glob("*.pem")):
        if _ID.match(path.stem):
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def _stamp() -> tuple:
    folder = trust_dir()
    if not folder.is_dir():
        return ()
    return tuple(sorted((path.name, path.stat().st_mtime_ns) for path in folder.glob("*.pem")))


def _sync_system() -> bool:
    script = Path("/usr/local/sbin/hibit-trust")
    if not script.is_file() or not sys.platform.startswith("linux"):
        return False
    command = [str(script)] if hasattr(os, "geteuid") and os.geteuid() == 0 else ["sudo", "-n", str(script)]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _drop_context() -> None:
    global _CTX, _STAMP
    _CTX = None
    _STAMP = None
