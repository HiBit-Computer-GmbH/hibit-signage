from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hibit_player import security
from hibit_player.config import credentials_dir, data_dir, ensure_dirs, media_dir
from hibit_player.validate import sanitize_state

_LOCK = threading.RLock()
SESSION_DAYS = 14


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _path() -> Path:
    ensure_dirs()
    return data_dir() / "player.db"


@contextmanager
def connect():
    conn = sqlite3.connect(_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    with _LOCK, connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS queue_items (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                origin TEXT NOT NULL,
                url TEXT,
                file_id TEXT,
                share_id TEXT,
                share_path TEXT,
                duration_seconds INTEGER,
                fit TEXT NOT NULL DEFAULT 'cover',
                muted INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT 'device',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shares (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                protocol TEXT NOT NULL,
                server TEXT NOT NULL,
                export_path TEXT NOT NULL,
                username TEXT,
                password TEXT,
                domain TEXT,
                options TEXT,
                created_at TEXT NOT NULL
            );
            """
        )


def ensure_admin(password: str | None) -> tuple[bool, str | None]:
    """Create the local admin if missing.

    Returns (created, generated_password). generated_password is set only when
    this process chose the password itself.
    """
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if row:
            return False, None
        chosen = password or security.new_token()[:16]
        generated = password is None
        conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, 'admin', ?, ?)",
            (str(uuid.uuid4()), security.hash_password(chosen), utcnow()),
        )
    note = data_dir() / "initial-password.txt"
    note.write_text(
        "HiBit Signage lokale Webkonsole\nBenutzer: admin\nPasswort: " + chosen + "\n",
        encoding="utf-8",
    )
    try:
        note.chmod(0o600)
    except OSError:
        pass
    return True, chosen if generated else None


def user_by_username(username: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def set_password(user_id: str, password: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (security.hash_password(password), user_id))


def create_session(user_id: str) -> str:
    token = security.new_token()
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).replace(microsecond=0).isoformat()
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (utcnow(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (security.token_hash(token), user_id, expires, utcnow()),
        )
    return token


def user_for_token(token: str) -> dict | None:
    if not token:
        return None
    with _LOCK, connect() as conn:
        row = conn.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at >= ?
            """,
            (security.token_hash(token), utcnow()),
        ).fetchone()
        return dict(row) if row else None


def delete_session(token: str) -> None:
    if not token:
        return
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (security.token_hash(token),))


def get_setting(key: str, default: str | None = None) -> str | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def hardware_id() -> str:
    current = get_setting("hardware_id")
    if current:
        return current
    machine = Path("/etc/machine-id")
    value = ""
    try:
        value = machine.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if not value:
        value = str(uuid.uuid4())
    set_setting("hardware_id", value)
    return value


def save_cloud_config(url: str | None, device_key: str | None) -> None:
    changed = False
    if url is not None:
        clean = url.strip().rstrip("/")
        if clean != (get_setting("cloud_url") or ""):
            set_setting("cloud_url", clean)
            changed = True
    if device_key is not None:
        clean_key = device_key.strip()
        if clean_key != (get_setting("device_key") or ""):
            set_setting("device_key", clean_key)
            changed = True
    if changed:
        set_setting("device_token", "")
        set_setting("applied_revision", "0")
        set_setting("dirty", "0")
        set_setting("cloud_status", "connecting" if get_setting("cloud_url") else "disabled")
        set_setting("cloud_name", "")
        set_setting("tenant_name", "")


def touch_playlist() -> int:
    current = int(get_setting("playlist_revision") or "0") + 1
    set_setting("playlist_revision", str(current))
    return current


def mark_local_edit() -> None:
    touch_playlist()
    if get_setting("device_token"):
        set_setting("dirty", "1")


def _row_queue(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["muted"] = bool(item["muted"])
    return item


def list_queue() -> list[dict]:
    with _LOCK, connect() as conn:
        rows = conn.execute("SELECT * FROM queue_items ORDER BY sort_order, created_at").fetchall()
        return [_row_queue(row) for row in rows]


def get_queue_item(item_id: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
        return _row_queue(row) if row else None


def insert_queue(item: dict) -> dict:
    item = dict(item)
    item.setdefault("id", str(uuid.uuid4()))
    item.setdefault("created_at", utcnow())
    with _LOCK, connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM queue_items").fetchone()["n"]
        if count >= 500:
            raise ValueError("Die Warteschlange kann höchstens 500 Einträge haben.")
        order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM queue_items").fetchone()["n"]
        conn.execute(
            """
            INSERT INTO queue_items (
                id, name, kind, origin, url, file_id, share_id, share_path,
                duration_seconds, fit, muted, enabled, sort_order, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                item["name"],
                item["kind"],
                item["origin"],
                item.get("url"),
                item.get("file_id"),
                item.get("share_id"),
                item.get("share_path"),
                item.get("duration_seconds"),
                item.get("fit") or "cover",
                1 if item.get("muted") else 0,
                1 if item.get("enabled", True) else 0,
                order,
                item["created_at"],
            ),
        )
    mark_local_edit()
    return get_queue_item(item["id"])


def update_queue(item_id: str, fields: dict) -> dict | None:
    current = get_queue_item(item_id)
    if not current:
        return None
    allowed = ("name", "duration_seconds", "fit", "muted", "enabled", "url")
    sets = []
    values = []
    for key in allowed:
        if key not in fields:
            continue
        value = fields[key]
        if key in ("muted", "enabled"):
            value = 1 if value else 0
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return current
    values.append(item_id)
    with _LOCK, connect() as conn:
        conn.execute(f"UPDATE queue_items SET {', '.join(sets)} WHERE id = ?", values)
    mark_local_edit()
    return get_queue_item(item_id)


def delete_queue(item_id: str) -> bool:
    with _LOCK, connect() as conn:
        cur = conn.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))
        deleted = cur.rowcount > 0
    if deleted:
        _normalize_order()
        mark_local_edit()
    return deleted


def reorder(ids: list[str]) -> None:
    existing = [item["id"] for item in list_queue()]
    if sorted(ids) != sorted(existing) or len(ids) != len(existing):
        raise ValueError("Die Reihenfolge enthält nicht genau die vorhandenen Einträge.")
    with _LOCK, connect() as conn:
        for index, item_id in enumerate(ids):
            conn.execute("UPDATE queue_items SET sort_order = ? WHERE id = ?", (index, item_id))
    mark_local_edit()


def _normalize_order() -> None:
    items = list_queue()
    with _LOCK, connect() as conn:
        for index, item in enumerate(items):
            conn.execute("UPDATE queue_items SET sort_order = ? WHERE id = ?", (index, item["id"]))


def list_files() -> list[dict]:
    with _LOCK, connect() as conn:
        rows = conn.execute("SELECT * FROM files ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]


def get_file(file_id: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row else None


def insert_file(item: dict) -> dict:
    item = dict(item)
    item.setdefault("id", str(uuid.uuid4()))
    item.setdefault("created_at", utcnow())
    item.setdefault("location", "device")
    with _LOCK, connect() as conn:
        conn.execute(
            """
            INSERT INTO files (id, name, stored_name, kind, size, sha256, location, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                item["name"],
                item["stored_name"],
                item["kind"],
                int(item["size"]),
                item["sha256"],
                item["location"],
                item["created_at"],
            ),
        )
    mark_local_edit()
    return get_file(item["id"])


def delete_file(file_id: str) -> dict | None:
    current = get_file(file_id)
    if not current:
        return None
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM queue_items WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    blob = media_dir() / current["stored_name"]
    try:
        blob.unlink(missing_ok=True)
    except OSError:
        pass
    mark_local_edit()
    return current


def list_shares() -> list[dict]:
    with _LOCK, connect() as conn:
        rows = conn.execute("SELECT * FROM shares ORDER BY name").fetchall()
        return [dict(row) for row in rows]


def get_share(share_id: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()
        return dict(row) if row else None


def insert_share(item: dict) -> dict:
    item = dict(item)
    item.setdefault("id", str(uuid.uuid4()))
    item.setdefault("created_at", utcnow())
    with _LOCK, connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM shares").fetchone()["n"]
        if count >= 50:
            raise ValueError("Es sind höchstens 50 Freigaben möglich.")
        conn.execute(
            """
            INSERT INTO shares (
                id, name, protocol, server, export_path, username, password, domain, options, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                item["name"],
                item["protocol"],
                item["server"],
                item["export_path"],
                item.get("username") or "",
                item.get("password") or "",
                item.get("domain") or "",
                item.get("options") or "",
                item["created_at"],
            ),
        )
    mark_local_edit()
    return get_share(item["id"])


def update_share(share_id: str, fields: dict) -> dict | None:
    current = get_share(share_id)
    if not current:
        return None
    merged = dict(current)
    merged.update(fields)
    if fields.get("password") in ("", None) and "password" in fields:
        merged["password"] = current.get("password") or ""
    with _LOCK, connect() as conn:
        conn.execute(
            """
            UPDATE shares
            SET name = ?, protocol = ?, server = ?, export_path = ?, username = ?, password = ?, domain = ?, options = ?
            WHERE id = ?
            """,
            (
                merged["name"],
                merged["protocol"],
                merged["server"],
                merged["export_path"],
                merged.get("username") or "",
                merged.get("password") or "",
                merged.get("domain") or "",
                merged.get("options") or "",
                share_id,
            ),
        )
    mark_local_edit()
    return get_share(share_id)


def delete_share(share_id: str) -> dict | None:
    current = get_share(share_id)
    if not current:
        return None
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM queue_items WHERE share_id = ?", (share_id,))
        conn.execute("DELETE FROM shares WHERE id = ?", (share_id,))
    cred = credentials_dir() / share_id
    try:
        cred.unlink(missing_ok=True)
    except OSError:
        pass
    mark_local_edit()
    return current


def export_state() -> dict:
    files = []
    for item in list_files():
        files.append(
            {
                "id": item["id"],
                "name": item["name"],
                "stored_name": item["stored_name"],
                "kind": item["kind"],
                "size": item["size"],
                "sha256": item["sha256"],
                "location": item["location"],
            }
        )
    shares = []
    for item in list_shares():
        shares.append(
            {
                "id": item["id"],
                "name": item["name"],
                "protocol": item["protocol"],
                "server": item["server"],
                "export_path": item["export_path"],
                "username": item.get("username") or "",
                "password": item.get("password") or "",
                "domain": item.get("domain") or "",
                "options": item.get("options") or "",
            }
        )
    queue = []
    for item in list_queue():
        queue.append(
            {
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "origin": item["origin"],
                "url": item.get("url"),
                "file_id": item.get("file_id"),
                "share_id": item.get("share_id"),
                "share_path": item.get("share_path"),
                "duration_seconds": item.get("duration_seconds"),
                "fit": item.get("fit") or "cover",
                "muted": bool(item.get("muted")),
                "enabled": bool(item.get("enabled")),
                "sort_order": item.get("sort_order") or 0,
            }
        )
    return sanitize_state({"files": files, "shares": shares, "queue": queue})


def replace_state(state: dict, revision: int) -> None:
    clean = sanitize_state(state)
    old_files = {item["id"]: item for item in list_files()}
    old_share_ids = {item["id"] for item in list_shares()}
    new_ids = {item["id"] for item in clean["files"]}
    new_share_ids = {item["id"] for item in clean["shares"]}
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM queue_items")
        conn.execute("DELETE FROM shares")
        conn.execute("DELETE FROM files")
        now = utcnow()
        for item in clean["files"]:
            conn.execute(
                """
                INSERT INTO files (id, name, stored_name, kind, size, sha256, location, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["name"],
                    item["stored_name"],
                    item["kind"],
                    int(item["size"]),
                    item["sha256"],
                    item["location"],
                    now,
                ),
            )
        for item in clean["shares"]:
            conn.execute(
                """
                INSERT INTO shares (
                    id, name, protocol, server, export_path, username, password, domain, options, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["name"],
                    item["protocol"],
                    item["server"],
                    item["export_path"],
                    item.get("username") or "",
                    item.get("password") or "",
                    item.get("domain") or "",
                    item.get("options") or "",
                    now,
                ),
            )
        for item in clean["queue"]:
            conn.execute(
                """
                INSERT INTO queue_items (
                    id, name, kind, origin, url, file_id, share_id, share_path,
                    duration_seconds, fit, muted, enabled, sort_order, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["name"],
                    item["kind"],
                    item["origin"],
                    item.get("url"),
                    item.get("file_id"),
                    item.get("share_id"),
                    item.get("share_path"),
                    item.get("duration_seconds"),
                    item.get("fit") or "cover",
                    1 if item.get("muted") else 0,
                    1 if item.get("enabled", True) else 0,
                    item.get("sort_order") or 0,
                    now,
                ),
            )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('applied_revision', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(int(revision)),),
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('dirty', '0') ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        current = conn.execute("SELECT value FROM settings WHERE key = 'playlist_revision'").fetchone()
        playlist = int(current["value"] if current else "0") + 1
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('playlist_revision', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(playlist),),
        )
    for file_id, item in old_files.items():
        if file_id in new_ids:
            continue
        blob = media_dir() / item["stored_name"]
        try:
            blob.unlink(missing_ok=True)
        except OSError:
            pass
    for share_id in old_share_ids - new_share_ids:
        cred = credentials_dir() / share_id
        try:
            cred.unlink(missing_ok=True)
        except OSError:
            pass


def sync_meta() -> dict:
    return {
        "applied_revision": int(get_setting("applied_revision") or "0"),
        "dirty": get_setting("dirty") == "1",
        "playlist_revision": int(get_setting("playlist_revision") or "0"),
        "cloud_status": get_setting("cloud_status") or "disabled",
    }


def state_is_empty(state: dict | None = None) -> bool:
    current = state if state is not None else export_state()
    return not current["queue"] and not current["shares"] and not current["files"]
