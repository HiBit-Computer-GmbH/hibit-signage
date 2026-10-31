from __future__ import annotations

import re
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".avif"}
VIDEO_EXT = {".mp4", ".webm", ".ogv", ".ogg", ".mov", ".m4v", ".mkv"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
STORED_RE = re.compile(r"^[0-9a-f-]{36}\.[A-Za-z0-9]{1,8}$", re.I)
SERVER_RE = re.compile(r"^[A-Za-z0-9._:\[\]-]+$")
EXPORT_RE = re.compile(r"^[A-Za-z0-9._@/+:-]+$")


def kind_for_name(name: str) -> str | None:
    ext = Path(name or "").suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return None


def kind_for_url(url: str) -> str | None:
    path = url.split("?", 1)[0].split("#", 1)[0]
    return kind_for_name(path)


def safe_filename(name: str) -> str:
    base = Path(name or "datei").name.strip() or "datei"
    base = re.sub(r"[\x00-\x1f]", "", base)
    return base[:180]


def is_uuid(value: str) -> bool:
    return bool(UUID_RE.match(value or ""))


def safe_relative(path: str) -> str:
    raw = (path or "").replace("\\", "/").lstrip("/")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError("Der Pfad darf nicht aus der Freigabe herausführen.")
    return "/".join(parts)


def validate_share(data: dict) -> dict:
    protocol = (data.get("protocol") or "").lower()
    if protocol not in ("smb", "nfs"):
        raise ValueError("Protokoll muss SMB oder NFS sein.")
    server = (data.get("server") or "").strip()
    export_path = (data.get("export_path") or "").strip().replace("\\", "/").lstrip("/")
    name = (data.get("name") or "").strip()[:120]
    if not name:
        raise ValueError("Die Freigabe braucht einen Namen.")
    if not SERVER_RE.match(server):
        raise ValueError("Servername oder IP ist ungültig.")
    if not export_path or not EXPORT_RE.match(export_path):
        raise ValueError("Freigabepfad ist ungültig.")
    if protocol == "smb" and "/" in export_path:
        raise ValueError("Der SMB-Freigabename darf keinen Schrägstrich enthalten.")
    return {
        "name": name,
        "protocol": protocol,
        "server": server,
        "export_path": export_path,
        "username": (data.get("username") or "").strip()[:120],
        "password": data.get("password") if isinstance(data.get("password"), str) else "",
        "domain": (data.get("domain") or "").strip()[:120],
        "options": (data.get("options") or "").strip()[:200],
    }


def sanitize_state(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Zustand ist ungültig.")
    files_in = data.get("files") or []
    shares_in = data.get("shares") or []
    queue_in = data.get("queue") or []
    if not isinstance(files_in, list) or not isinstance(shares_in, list) or not isinstance(queue_in, list):
        raise ValueError("Zustand ist ungültig.")
    if len(files_in) > 2000 or len(shares_in) > 50 or len(queue_in) > 500:
        raise ValueError("Zustand überschreitet die zulässige Größe.")

    files = []
    seen_files: set[str] = set()
    for item in files_in:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("id") or "")
        stored = str(item.get("stored_name") or "")
        if not is_uuid(fid) or fid in seen_files or not STORED_RE.match(stored):
            continue
        location = item.get("location") if item.get("location") in ("cloud", "device") else "device"
        kind = item.get("kind") if item.get("kind") in ("image", "video") else kind_for_name(stored)
        if kind not in ("image", "video"):
            continue
        seen_files.add(fid)
        files.append(
            {
                "id": fid,
                "name": safe_filename(str(item.get("name") or stored)),
                "stored_name": stored,
                "kind": kind,
                "size": int(item.get("size") or 0),
                "sha256": str(item.get("sha256") or "")[:64],
                "location": location,
            }
        )

    shares = []
    seen_shares: set[str] = set()
    for item in shares_in:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "")
        if not is_uuid(sid) or sid in seen_shares:
            continue
        try:
            clean = validate_share(item)
        except ValueError:
            continue
        seen_shares.add(sid)
        shares.append({"id": sid, **clean})

    queue = []
    seen_queue: set[str] = set()
    for index, item in enumerate(queue_in):
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "")
        if not is_uuid(qid) or qid in seen_queue:
            continue
        kind = item.get("kind")
        origin = item.get("origin")
        if kind not in ("image", "video", "web") or origin not in ("url", "local", "share"):
            continue
        if kind == "web" and origin != "url":
            continue
        duration = item.get("duration_seconds")
        if duration is not None:
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                duration = None
            if duration is not None and not 1 <= duration <= 86400:
                duration = None
        if kind == "image" and duration is None:
            duration = 10
        if kind == "web" and duration is None:
            duration = 30
        entry = {
            "id": qid,
            "name": (str(item.get("name") or "Inhalt")[:180] or "Inhalt"),
            "kind": kind,
            "origin": origin,
            "url": None,
            "file_id": None,
            "share_id": None,
            "share_path": None,
            "duration_seconds": duration,
            "fit": "contain" if item.get("fit") == "contain" else "cover",
            "muted": bool(item.get("muted")),
            "enabled": bool(item.get("enabled", True)),
            "sort_order": index,
        }
        if origin == "url":
            url = str(item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")) or len(url) > 2000:
                continue
            entry["url"] = url
        elif origin == "local":
            file_id = str(item.get("file_id") or "")
            if not is_uuid(file_id):
                continue
            entry["file_id"] = file_id
        else:
            share_id = str(item.get("share_id") or "")
            if not is_uuid(share_id):
                continue
            try:
                entry["share_path"] = safe_relative(str(item.get("share_path") or ""))
            except ValueError:
                continue
            if not entry["share_path"]:
                continue
            entry["share_id"] = share_id
        seen_queue.add(qid)
        queue.append(entry)
    return {"files": files, "shares": shares, "queue": queue}
