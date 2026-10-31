from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from hibit_player.config import credentials_dir, mount_root
from hibit_player.db import get_share, list_shares
from hibit_player.validate import kind_for_name, safe_relative

log = logging.getLogger("hibit.player.mounts")
_ERRORS: dict[str, str] = {}


def last_error(share_id: str) -> str:
    return _ERRORS.get(share_id, "")


def status_map() -> dict[str, dict]:
    result = {}
    for share in list_shares():
        path = mount_path(share)
        result[share["id"]] = {
            "mounted": is_mounted(path),
            "error": _ERRORS.get(share["id"], ""),
            "mount_path": str(path),
        }
    return result


def mount_path(share: dict) -> Path:
    root = mount_root().resolve()
    path = (root / share["id"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Ungültiger Einhängepunkt.")
    return path


def is_mounted(path: Path) -> bool:
    target = str(path)
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and _unescape(parts[1]) == target:
                    return True
    except OSError:
        return False
    return False


def _unescape(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def _write_credentials(share: dict) -> Path:
    directory = credentials_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / share["id"]
    lines = [
        f"username={share.get('username') or 'guest'}",
        f"password={share.get('password') or ''}",
        f"domain={share.get('domain') or 'WORKGROUP'}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _redact(text: str, password: str) -> str:
    if password:
        text = text.replace(password, "••••")
    return text.strip()[-800:]


def mount_share(share: dict) -> None:
    if not sys.platform.startswith("linux"):
        _ERRORS[share["id"]] = "SMB- und NFS-Freigaben können nur unter Linux eingehängt werden."
        raise RuntimeError(_ERRORS[share["id"]])
    path = mount_path(share)
    path.mkdir(parents=True, exist_ok=True)
    if is_mounted(path):
        _ERRORS.pop(share["id"], None)
        return
    uid = str(os.getuid())
    gid = str(os.getgid())
    helper = Path("/usr/local/sbin/hibit-mount")
    if share["protocol"] == "smb":
        creds = _write_credentials(share)
        command = ["mount", "smb", share["server"], share["export_path"], str(path), str(creds), uid, gid]
    else:
        command = ["mount", "nfs", share["server"], share["export_path"], str(path), "-", uid, gid]
    if helper.exists():
        command = ["sudo", "-n", str(helper), *command]
    else:
        command = _direct_mount(share, path, uid, gid)
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=40, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        _ERRORS[share["id"]] = str(exc)
        raise RuntimeError(str(exc)) from exc
    if proc.returncode != 0:
        message = _redact((proc.stderr or proc.stdout or "Einhängen fehlgeschlagen.").strip(), share.get("password") or "")
        _ERRORS[share["id"]] = message or "Einhängen fehlgeschlagen."
        raise RuntimeError(_ERRORS[share["id"]])
    _ERRORS.pop(share["id"], None)


def _direct_mount(share: dict, path: Path, uid: str, gid: str) -> list[str]:
    """Fallback when the appliance helper is not installed and the process may already be root."""
    if share["protocol"] == "smb":
        creds = _write_credentials(share)
        options = f"credentials={creds},ro,uid={uid},gid={gid},file_mode=0444,dir_mode=0555,iocharset=utf8,noperm,noserverino"
        return ["mount", "-t", "cifs", f"//{share['server']}/{share['export_path']}", str(path), "-o", options]
    return ["mount", "-t", "nfs", f"{share['server']}:/{share['export_path']}", str(path), "-o", "ro,soft,timeo=30,retrans=2,vers=4.1"]


def unmount_share(share: dict) -> None:
    path = mount_path(share)
    if not sys.platform.startswith("linux") or not is_mounted(path):
        return
    helper = Path("/usr/local/sbin/hibit-mount")
    command = ["umount", str(path)]
    if helper.exists():
        command = ["sudo", "-n", str(helper), *command]
    else:
        command = ["umount", str(path)]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    if proc.returncode != 0 and is_mounted(path):
        message = (proc.stderr or proc.stdout or "Aushängen fehlgeschlagen.").strip()
        _ERRORS[share["id"]] = message
        raise RuntimeError(message)


def sync_shares(shares: list[dict] | None = None) -> dict[str, str]:
    desired = shares if shares is not None else list_shares()
    desired_ids = {item["id"] for item in desired}
    if sys.platform.startswith("linux"):
        root = mount_root()
        try:
            for child in root.iterdir():
                if child.name not in desired_ids and is_mounted(child):
                    helper = Path("/usr/local/sbin/hibit-mount")
                    command = ["umount", str(child)]
                    if helper.exists():
                        command = ["sudo", "-n", str(helper), *command]
                    subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        except OSError:
            pass
    errors: dict[str, str] = {}
    for share in desired:
        try:
            if sys.platform.startswith("linux"):
                path = mount_path(share)
                if is_mounted(path):
                    try:
                        unmount_share(share)
                    except RuntimeError:
                        log.warning("Freigabe %s konnte vor dem erneuten Einhängen nicht gelöst werden", share["id"])
            mount_share(share)
        except RuntimeError as exc:
            errors[share["id"]] = str(exc)
    return errors


def browse(share_id: str, relative: str = "") -> dict:
    share = get_share(share_id)
    if not share:
        return {"error": "Freigabe nicht gefunden.", "entries": [], "path": relative}
    try:
        relative = safe_relative(relative)
        if not is_mounted(mount_path(share)):
            mount_share(share)
        root = mount_path(share).resolve()
        target = (root / relative).resolve() if relative else root
        if not target.is_relative_to(root):
            return {"error": "Ungültiger Pfad.", "entries": [], "path": relative}
        if not target.is_dir():
            return {"error": "Ordner nicht gefunden.", "entries": [], "path": relative}
        entries = []
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if child.name.startswith("."):
                continue
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(root):
                continue
            rel = resolved.relative_to(root).as_posix()
            is_dir = child.is_dir()
            entries.append(
                {
                    "name": child.name,
                    "path": rel,
                    "dir": is_dir,
                    "kind": None if is_dir else kind_for_name(child.name),
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return {"error": None, "entries": entries, "path": relative}
    except (RuntimeError, ValueError, OSError) as exc:
        return {"error": str(exc), "entries": [], "path": relative}
