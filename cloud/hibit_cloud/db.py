from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hibit_cloud import security
from hibit_cloud.config import data_dir, ensure_dirs, files_dir
from hibit_cloud.validate import sanitize_state

_LOCK = threading.RLock()
SESSION_DAYS = 14
EMPTY_STATE = {"queue": [], "shares": [], "files": []}


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _path() -> Path:
    ensure_dirs()
    return data_dir() / "cloud.db"


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
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                tenant_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS enrollment_keys (
                id TEXT PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                label TEXT NOT NULL,
                tenant_id TEXT,
                max_uses INTEGER,
                uses INTEGER NOT NULL DEFAULT 0,
                revoked INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                hardware_id TEXT UNIQUE,
                hostname TEXT,
                name TEXT,
                status TEXT NOT NULL,
                tenant_id TEXT,
                enrollment_key_id TEXT,
                token_hash TEXT,
                local_ips TEXT,
                version TEXT,
                last_seen TEXT,
                state_json TEXT NOT NULL DEFAULT '{}',
                state_revision INTEGER NOT NULL DEFAULT 0,
                runtime_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    for name, ddl in (
        ("totp_secret", "TEXT"),
        ("totp_pending", "TEXT"),
        ("totp_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("recovery_hashes", "TEXT"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS login_tickets (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            target_id TEXT NOT NULL DEFAULT '',
            run_at TEXT NOT NULL,
            leased_until TEXT,
            created_at TEXT NOT NULL
        );
        """
    )


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "mandant"
    return base[:48]


def ensure_admin(email: str | None, password: str | None, name: str | None = None) -> tuple[bool, str | None]:
    """Create the first cloud admin if the database has no users.

    Returns (created, generated_password).
    """
    with _LOCK, connect() as conn:
        existing = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        if existing:
            return False, None
        chosen = password or security.new_token()[:18]
        generated = password is None
        mail = (email or "admin@hibit.local").strip().lower()
        conn.execute(
            """
            INSERT INTO users (id, email, name, password_hash, role, tenant_id, created_at)
            VALUES (?, ?, ?, ?, 'superadmin', NULL, ?)
            """,
            (str(uuid.uuid4()), mail, name or "Cloud-Admin", security.hash_password(chosen), utcnow()),
        )
    note = data_dir() / "initial-password.txt"
    note.write_text(f"HiBit Signage Cloud\nE-Mail: {mail}\nPasswort: {chosen}\n", encoding="utf-8")
    try:
        note.chmod(0o600)
    except OSError:
        pass
    return True, chosen if generated else None


def user_by_email(email: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None


def user_by_id(user_id: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def public_user(user: dict | None) -> dict | None:
    if not user:
        return None
    tenant = get_tenant(user["tenant_id"]) if user.get("tenant_id") else None
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "tenant_id": user.get("tenant_id"),
        "tenant_name": tenant["name"] if tenant else None,
        "totp_enabled": bool(user.get("totp_enabled")),
    }


def list_users() -> list[dict]:
    with _LOCK, connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
    return [public_user(dict(row)) for row in rows]


def count_role(role: str) -> int:
    with _LOCK, connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = ?", (role,)).fetchone()["n"]


def create_user(email: str, name: str, password: str, role: str, tenant_id: str | None) -> dict:
    with _LOCK, connect() as conn:
        user_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO users (id, email, name, password_hash, role, tenant_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, email.strip().lower(), name.strip(), security.hash_password(password), role, tenant_id, utcnow()),
        )
    return public_user(user_by_id(user_id))


def update_user(user_id: str, fields: dict) -> dict | None:
    current = user_by_id(user_id)
    if not current:
        return None
    name = fields.get("name", current["name"])
    role = fields.get("role", current["role"])
    tenant_id = fields.get("tenant_id", current["tenant_id"])
    email = fields.get("email", current["email"]).strip().lower()
    with _LOCK, connect() as conn:
        conn.execute(
            "UPDATE users SET name = ?, role = ?, tenant_id = ?, email = ? WHERE id = ?",
            (name, role, tenant_id, email, user_id),
        )
        if fields.get("password"):
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (security.hash_password(fields["password"]), user_id),
            )
    return public_user(user_by_id(user_id))


def delete_user(user_id: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


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


def set_totp_pending(user_id: str, secret: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE users SET totp_pending = ? WHERE id = ?", (secret, user_id))


def totp_pending(user_id: str) -> str:
    user = user_by_id(user_id)
    return (user or {}).get("totp_pending") or ""


def enable_totp(user_id: str, secret: str, codes: list[str]) -> None:
    hashes = json.dumps([security.recovery_hash(code) for code in codes])
    with _LOCK, connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET totp_secret = ?, totp_pending = NULL, totp_enabled = 1, recovery_hashes = ?
            WHERE id = ?
            """,
            (secret, hashes, user_id),
        )


def disable_totp(user_id: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET totp_secret = NULL, totp_pending = NULL, totp_enabled = 0, recovery_hashes = NULL
            WHERE id = ?
            """,
            (user_id,),
        )


def recovery_codes(user_id: str) -> list[str]:
    user = user_by_id(user_id) or {}
    try:
        values = json.loads(user.get("recovery_hashes") or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in values if isinstance(item, str)]


def consume_recovery(user_id: str, code: str) -> bool:
    digest = security.recovery_hash(code)
    hashes = recovery_codes(user_id)
    kept = []
    matched = False
    for item in hashes:
        if not matched and security.hashes_equal(item, digest):
            matched = True
            continue
        kept.append(item)
    if not matched:
        return False
    with _LOCK, connect() as conn:
        conn.execute("UPDATE users SET recovery_hashes = ? WHERE id = ?", (json.dumps(kept), user_id))
    return True


def create_ticket(user_id: str) -> str:
    token = security.new_token()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0).isoformat()
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM login_tickets WHERE expires_at < ?", (utcnow(),))
        conn.execute(
            "INSERT INTO login_tickets (token_hash, user_id, attempts, expires_at, created_at) VALUES (?, ?, 0, ?, ?)",
            (security.token_hash(token), user_id, expires, utcnow()),
        )
    return token


def ticket_user(token: str) -> dict | None:
    if not token:
        return None
    with _LOCK, connect() as conn:
        row = conn.execute(
            """
            SELECT users.* FROM login_tickets
            JOIN users ON users.id = login_tickets.user_id
            WHERE login_tickets.token_hash = ? AND login_tickets.expires_at >= ?
            """,
            (security.token_hash(token), utcnow()),
        ).fetchone()
        return dict(row) if row else None


def ticket_failed(token: str) -> None:
    if not token:
        return
    digest = security.token_hash(token)
    with _LOCK, connect() as conn:
        conn.execute("UPDATE login_tickets SET attempts = attempts + 1 WHERE token_hash = ?", (digest,))
        conn.execute("DELETE FROM login_tickets WHERE token_hash = ? AND attempts >= 5", (digest,))


def delete_ticket(token: str) -> None:
    if not token:
        return
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM login_tickets WHERE token_hash = ?", (security.token_hash(token),))


def schedule_job(kind: str, target_id: str, run_at: str) -> dict:
    target = target_id or ""
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind = ? AND IFNULL(target_id, '') = ?", (kind, target))
        job_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO jobs (id, kind, target_id, run_at, leased_until, created_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            (job_id, kind, target, run_at, utcnow()),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row)


def job_for(kind: str, target_id: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE kind = ? AND IFNULL(target_id, '') = ?",
            (kind, target_id or ""),
        ).fetchone()
        return dict(row) if row else None


def cancel_job(kind: str, target_id: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind = ? AND IFNULL(target_id, '') = ?", (kind, target_id or ""))


def due_jobs() -> list[dict]:
    now = utcnow()
    with _LOCK, connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE run_at <= ? AND (leased_until IS NULL OR leased_until <= ?)
            ORDER BY run_at
            """,
            (now, now),
        ).fetchall()
        return [dict(row) for row in rows]


def lease_job(job_id: str, seconds: int = 180) -> bool:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    until = (now + timedelta(seconds=seconds)).isoformat()
    stamp = now.isoformat()
    with _LOCK, connect() as conn:
        cur = conn.execute(
            """
            UPDATE jobs SET leased_until = ?
            WHERE id = ? AND run_at <= ? AND (leased_until IS NULL OR leased_until <= ?)
            """,
            (until, job_id, stamp, stamp),
        )
        return cur.rowcount == 1


def release_job(job_id: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE jobs SET leased_until = NULL WHERE id = ?", (job_id,))


def delete_job(job_id: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def delete_session(token: str) -> None:
    if not token:
        return
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (security.token_hash(token),))


def list_tenants() -> list[dict]:
    with _LOCK, connect() as conn:
        rows = conn.execute("SELECT * FROM tenants ORDER BY name").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["devices"] = conn.execute(
                "SELECT COUNT(*) AS n FROM devices WHERE tenant_id = ? AND status = 'adopted'",
                (item["id"],),
            ).fetchone()["n"]
            item["users"] = conn.execute("SELECT COUNT(*) AS n FROM users WHERE tenant_id = ?", (item["id"],)).fetchone()["n"]
            result.append(item)
        return result


def get_tenant(tenant_id: str | None) -> dict | None:
    if not tenant_id:
        return None
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
        return dict(row) if row else None


def create_tenant(name: str) -> dict:
    base = _slug(name)
    with _LOCK, connect() as conn:
        slug = base
        suffix = 2
        while conn.execute("SELECT 1 FROM tenants WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base}-{suffix}"
            suffix += 1
        tenant_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO tenants (id, name, slug, created_at) VALUES (?, ?, ?, ?)",
            (tenant_id, name.strip(), slug, utcnow()),
        )
    return get_tenant(tenant_id)


def rename_tenant(tenant_id: str, name: str) -> dict | None:
    if not get_tenant(tenant_id):
        return None
    with _LOCK, connect() as conn:
        conn.execute("UPDATE tenants SET name = ? WHERE id = ?", (name.strip(), tenant_id))
    return get_tenant(tenant_id)


def delete_tenant(tenant_id: str) -> None:
    with _LOCK, connect() as conn:
        devices = conn.execute("SELECT COUNT(*) AS n FROM devices WHERE tenant_id = ?", (tenant_id,)).fetchone()["n"]
        users = conn.execute("SELECT COUNT(*) AS n FROM users WHERE tenant_id = ?", (tenant_id,)).fetchone()["n"]
        if devices or users:
            raise ValueError("Der Mandant hat noch Geräte oder Benutzer.")
        conn.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))


def list_keys() -> list[dict]:
    with _LOCK, connect() as conn:
        rows = conn.execute("SELECT * FROM enrollment_keys ORDER BY created_at DESC").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["revoked"] = bool(item["revoked"])
        tenant = get_tenant(item.get("tenant_id"))
        item["tenant_name"] = tenant["name"] if tenant else None
        result.append(item)
    return result


def create_key(label: str, tenant_id: str | None, max_uses: int | None, created_by: str | None) -> dict:
    key = security.new_enrollment_key()
    key_id = str(uuid.uuid4())
    with _LOCK, connect() as conn:
        conn.execute(
            """
            INSERT INTO enrollment_keys (id, key, label, tenant_id, max_uses, uses, revoked, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)
            """,
            (key_id, key, label.strip(), tenant_id, max_uses, created_by, utcnow()),
        )
        row = conn.execute("SELECT * FROM enrollment_keys WHERE id = ?", (key_id,)).fetchone()
        return dict(row)


def get_key(key_id: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM enrollment_keys WHERE id = ?", (key_id,)).fetchone()
        return dict(row) if row else None


def key_by_secret(secret: str) -> dict | None:
    normalized = security.normalize_enrollment_key(secret)
    if not normalized:
        return None
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM enrollment_keys WHERE key = ?", (normalized,)).fetchone()
        return dict(row) if row else None


def revoke_key(key_id: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE enrollment_keys SET revoked = 1 WHERE id = ?", (key_id,))


def delete_key(key_id: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM enrollment_keys WHERE id = ?", (key_id,))


def _device(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    item = dict(row)
    try:
        item["ips"] = json.loads(item.get("local_ips") or "[]")
    except json.JSONDecodeError:
        item["ips"] = []
    try:
        item["runtime"] = json.loads(item.get("runtime_json") or "{}")
    except json.JSONDecodeError:
        item["runtime"] = {}
    tenant = get_tenant(item.get("tenant_id"))
    item["tenant_name"] = tenant["name"] if tenant else None
    return item


def list_devices() -> list[dict]:
    with _LOCK, connect() as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY created_at DESC").fetchall()
    return [_device(row) for row in rows]


def get_device(device_id: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return _device(row)


def device_by_hardware(hardware_id: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM devices WHERE hardware_id = ?", (hardware_id,)).fetchone()
    return _device(row)


def device_by_token_hash(token_hash: str) -> dict | None:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM devices WHERE token_hash = ?", (token_hash,)).fetchone()
    return _device(row)


def insert_device(hardware_id: str, hostname: str, version: str, ips: list[str], key_row: dict, token: str) -> dict:
    device_id = str(uuid.uuid4())
    with _LOCK, connect() as conn:
        conn.execute(
            """
            INSERT INTO devices (
                id, hardware_id, hostname, name, status, tenant_id, enrollment_key_id, token_hash,
                local_ips, version, last_seen, state_json, state_revision, runtime_json, created_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, '{}', 0, '{}', ?)
            """,
            (
                device_id,
                hardware_id,
                hostname,
                hostname or "Neues Gerät",
                key_row.get("tenant_id"),
                key_row["id"],
                security.token_hash(token),
                json.dumps(ips),
                version,
                utcnow(),
                utcnow(),
            ),
        )
        conn.execute("UPDATE enrollment_keys SET uses = uses + 1 WHERE id = ?", (key_row["id"],))
    return get_device(device_id)


def rotate_token(device_id: str, token: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE devices SET token_hash = ? WHERE id = ?", (security.token_hash(token), device_id))


def touch_device(device_id: str, hostname: str, version: str, ips: list[str], runtime: dict | None = None) -> None:
    with _LOCK, connect() as conn:
        if runtime is None:
            conn.execute(
                "UPDATE devices SET hostname = ?, version = ?, local_ips = ?, last_seen = ? WHERE id = ?",
                (hostname, version, json.dumps(ips), utcnow(), device_id),
            )
        else:
            conn.execute(
                """
                UPDATE devices
                SET hostname = ?, version = ?, local_ips = ?, last_seen = ?, runtime_json = ?
                WHERE id = ?
                """,
                (hostname, version, json.dumps(ips), utcnow(), json.dumps(runtime), device_id),
            )


def adopt_device(device_id: str, name: str, tenant_id: str) -> dict | None:
    with _LOCK, connect() as conn:
        conn.execute(
            "UPDATE devices SET status = 'adopted', name = ?, tenant_id = ? WHERE id = ?",
            (name.strip(), tenant_id, device_id),
        )
    return get_device(device_id)


def move_device(device_id: str, tenant_id: str) -> dict | None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE devices SET tenant_id = ? WHERE id = ?", (tenant_id, device_id))
    return get_device(device_id)


def rename_device(device_id: str, name: str) -> dict | None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE devices SET name = ? WHERE id = ?", (name.strip(), device_id))
    return get_device(device_id)


def delete_device(device_id: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    folder = files_dir() / device_id
    if folder.exists():
        for child in folder.glob("*"):
            try:
                child.unlink()
            except OSError:
                pass
        try:
            folder.rmdir()
        except OSError:
            pass


def load_state(device_id: str) -> tuple[dict, int]:
    device = get_device(device_id)
    if not device:
        return dict(EMPTY_STATE), 0
    try:
        raw = json.loads(device.get("state_json") or "{}")
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return sanitize_state(raw), int(device.get("state_revision") or 0)


def save_state(device_id: str, state: dict, revision: int) -> dict:
    clean = sanitize_state(state)
    with _LOCK, connect() as conn:
        conn.execute(
            "UPDATE devices SET state_json = ?, state_revision = ? WHERE id = ?",
            (json.dumps(clean, ensure_ascii=False), int(revision), device_id),
        )
    return clean


def key_can_enroll(key_row: dict) -> str | None:
    if not key_row or key_row.get("revoked"):
        return "Der Einschreibeschlüssel ist ungültig."
    max_uses = key_row.get("max_uses")
    if max_uses is not None and int(key_row.get("uses") or 0) >= int(max_uses):
        return "Der Einschreibeschlüssel wurde bereits vollständig verwendet."
    return None
