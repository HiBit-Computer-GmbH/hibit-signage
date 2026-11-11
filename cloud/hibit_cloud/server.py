from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import mimetypes
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from hibit_cloud import db, hub as hub_module, security, updater
from hibit_cloud.config import data_dir, files_dir, install_dir, version
from hibit_cloud.hub import hub
from hibit_cloud.validate import kind_for_name, kind_for_url, safe_filename, safe_relative, validate_share

log = logging.getLogger("hibit.cloud")
STATIC = Path(__file__).resolve().parents[1] / "static"
SHARED = Path(__file__).resolve().parents[2] / "shared"
COOKIE = "hibit_session"
MAX_UPLOAD = 4 * 1024 * 1024 * 1024
_FAILS: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    if os.environ.get("HIBIT_TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return urlparse(origin).netloc == (request.headers.get("host") or "")


def _cookie_secure(request: Request) -> bool:
    if os.environ.get("HIBIT_COOKIE_SECURE") == "1":
        return True
    if os.environ.get("HIBIT_TRUST_PROXY") == "1" and request.headers.get("x-forwarded-proto") == "https":
        return True
    return request.url.scheme == "https"


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "-c", f"safe.directory={install_dir()}", "rev-parse", "--short", "HEAD"],
            cwd=install_dir(),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _html(path: Path) -> HTMLResponse:
    text = path.read_text(encoding="utf-8").replace("{{version}}", version())
    return HTMLResponse(text, headers={"Cache-Control": "no-cache"})


def _static(path: Path, media_type: str) -> FileResponse:
    if not path.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-cache"})


async def _json(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Ungültiges JSON.") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "Ungültiges JSON.")
    return data


def _kind_from_request(data: dict, url: str) -> str:
    raw = data.get("kind")
    requested = str(raw).strip() if raw else ""
    if requested == "web":
        return "web"
    return requested or kind_for_url(url) or ""


def _fit(data: dict, kind: str) -> str:
    value = str(data.get("fit") or "")
    if value in {"contain", "cover"}:
        return value
    return "contain" if kind == "image" else "cover"


def _duration(value, kind: str):
    if value is None or value == "":
        if kind == "video":
            return None
        return 30 if kind == "web" else 10
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Die Dauer ist ungültig.") from exc
    if not 1 <= number <= 86400:
        raise HTTPException(400, "Die Dauer muss zwischen 1 und 86400 Sekunden liegen.")
    return number


def _scroll(value) -> int:
    if value is None or value == "":
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Die Scrollgeschwindigkeit ist ungültig.") from exc
    if not 0 <= number <= 4000:
        raise HTTPException(400, "Die Scrollgeschwindigkeit muss zwischen 0 und 4000 Pixeln pro Sekunde liegen.")
    return number


def _color_scheme(value) -> str:
    scheme = str(value or "auto").strip().lower()
    if scheme in {"light", "dark", "auto"}:
        return scheme
    raise HTTPException(400, "Das Farbschema muss automatisch, hell oder dunkel sein.")


def _url(value) -> str:
    url = str(value or "").strip()
    if not url.startswith(("http://", "https://")) or len(url) > 2000:
        raise HTTPException(400, "Die Adresse muss mit http:// oder https:// beginnen.")
    return url


def _zoom(value) -> int:
    if value is None or value == "":
        return 100
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Der Zoom ist ungültig.") from exc
    if not 100 <= number <= 300:
        raise HTTPException(400, "Der Zoom muss zwischen 100 und 300 Prozent liegen.")
    return number


def _visible(user: dict, device: dict) -> bool:
    if user["role"] == "superadmin":
        return True
    if device.get("tenant_id") != user.get("tenant_id"):
        return False
    if device["status"] == "adopted":
        return True
    return user["role"] == "tenant_admin" and device["status"] == "pending"


def _summary(device: dict) -> dict:
    return {
        "id": device["id"],
        "name": device.get("name") or "",
        "hostname": device.get("hostname") or "",
        "status": device["status"],
        "tenant_id": device.get("tenant_id"),
        "tenant_name": device.get("tenant_name"),
        "ips": device.get("ips") or [],
        "version": device.get("version") or "",
        "last_seen": device.get("last_seen"),
        "state_revision": device.get("state_revision") or 0,
        "runtime": device.get("runtime") or {},
        "online": hub.online(device["id"]),
        "created_at": device.get("created_at"),
        "hardware_id": device.get("hardware_id") or "",
        "update_at": (db.job_for("device-update", device["id"]) or {}).get("run_at"),
    }


def _require_device(user: dict, device_id: str, adopted: bool = False) -> dict:
    device = db.get_device(device_id)
    if not device or not _visible(user, device):
        raise HTTPException(404, "Gerät nicht gefunden")
    if adopted and device["status"] != "adopted":
        raise HTTPException(409, "Das Gerät ist noch nicht adoptiert.")
    return device


def _mutate(device_id: str, mutator) -> dict:
    state, revision = db.load_state(device_id)
    mutator(state)
    return db.save_state(device_id, state, revision + 1)


async def _push(device_id: str) -> None:
    await hub.push_state(device_id)


def _when(value) -> str:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(400, "Der Zeitpunkt ist ungültig.") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc).replace(microsecond=0)
    if moment < datetime.now(timezone.utc) - timedelta(minutes=2):
        raise HTTPException(400, "Der Zeitpunkt liegt in der Vergangenheit.")
    return moment.isoformat()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    _created, secret = db.ensure_admin(os.environ.get("HIBIT_ADMIN_EMAIL"), os.environ.get("HIBIT_ADMIN_PASSWORD"), os.environ.get("HIBIT_ADMIN_NAME"))
    if secret:
        print(f"HIBIT_ADMIN_CREATED {(os.environ.get('HIBIT_ADMIN_EMAIL') or 'admin@hibit.local')} {secret}", flush=True)
    stop = asyncio.Event()

    async def scheduled() -> None:
        while not stop.is_set():
            try:
                await hub.dispatch_updates()
            except Exception:
                log.exception("Geplante Updates")
            try:
                await asyncio.wait_for(stop.wait(), 20)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(scheduled())
    try:
        yield
    finally:
        stop.set()
        task.cancel()


app = FastAPI(title="HiBit Signage Cloud", version=version(), docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/agent/") or path in {"/", "/cloud.js", "/cloud.css", "/terminal.js", "/favicon.ico", "/logo-on-dark.png", "/logo-on-light.png"}:
        return await call_next(request)
    if request.method == "GET" and path == "/api/health":
        return await call_next(request)
    if request.method == "POST" and path in {"/api/login", "/api/login/totp"}:
        return await call_next(request)
    if path.startswith("/api/"):
        user = db.user_for_token(request.cookies.get(COOKIE, ""))
        if not user:
            return JSONResponse({"detail": "Anmeldung erforderlich"}, status_code=401)
        request.state.user = user
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _origin_ok(request):
            return JSONResponse({"detail": "Origin abgelehnt"}, status_code=403)
    return await call_next(request)


@app.get("/")
def root():
    return _html(STATIC / "index.html")


@app.get("/cloud.js")
def cloud_js():
    return _static(STATIC / "cloud.js", "text/javascript")


@app.get("/cloud.css")
def cloud_css():
    return _static(STATIC / "cloud.css", "text/css")


@app.get("/logo-on-dark.png")
def logo_on_dark():
    return _static(STATIC / "logo-on-dark.png", "image/png")


@app.get("/logo-on-light.png")
def logo_on_light():
    return _static(STATIC / "logo-on-light.png", "image/png")


@app.get("/terminal.js")
def terminal_js():
    return _static(SHARED / "terminal.js", "text/javascript")


@app.get("/api/health")
def health():
    return {"ok": True, "version": version(), "service": "cloud"}


@app.post("/api/login")
async def login(request: Request):
    ip = _client_ip(request)
    now = time.time()
    recent = [stamp for stamp in _FAILS.get(ip, []) if now - stamp < 600]
    if len(recent) >= 8:
        raise HTTPException(429, "Zu viele Versuche. Bitte kurz warten.")
    if not _origin_ok(request):
        raise HTTPException(403, "Origin abgelehnt")
    data = await _json(request)
    user = db.user_by_email(str(data.get("email") or ""))
    if not user or not security.verify_password(str(data.get("password") or ""), user["password_hash"]):
        recent.append(now)
        _FAILS[ip] = recent
        raise HTTPException(401, "E-Mail oder Passwort ist falsch.")
    _FAILS.pop(ip, None)
    if user.get("totp_enabled"):
        return {"totp_required": True, "ticket": db.create_ticket(user["id"])}
    token = db.create_session(user["id"])
    response = JSONResponse(db.public_user(user))
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=14 * 24 * 3600, secure=_cookie_secure(request), path="/")
    return response


@app.post("/api/login/totp")
async def login_totp(request: Request):
    ip = _client_ip(request)
    now = time.time()
    recent = [stamp for stamp in _FAILS.get(ip, []) if now - stamp < 600]
    if len(recent) >= 8:
        raise HTTPException(429, "Zu viele Versuche. Bitte kurz warten.")
    if not _origin_ok(request):
        raise HTTPException(403, "Origin abgelehnt")
    data = await _json(request)
    ticket = str(data.get("ticket") or "")
    user = db.ticket_user(ticket)
    if not user or not user.get("totp_enabled"):
        raise HTTPException(401, "Die Anmeldung ist abgelaufen. Bitte erneut anmelden.")
    code = str(data.get("code") or "")
    ok = security.totp_valid(user.get("totp_secret") or "", code) or db.consume_recovery(user["id"], code)
    if not ok:
        db.ticket_failed(ticket)
        recent.append(now)
        _FAILS[ip] = recent
        raise HTTPException(400, "Der Code ist falsch.")
    _FAILS.pop(ip, None)
    db.delete_ticket(ticket)
    token = db.create_session(user["id"])
    response = JSONResponse(db.public_user(user))
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=14 * 24 * 3600, secure=_cookie_secure(request), path="/")
    return response


@app.post("/api/logout")
def logout(request: Request):
    db.delete_session(request.cookies.get(COOKIE, ""))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@app.get("/api/me")
def me(request: Request):
    return db.public_user(request.state.user)


@app.post("/api/me/password")
async def change_password(request: Request):
    data = await _json(request)
    user = request.state.user
    nxt = str(data.get("next") or "")
    if len(nxt) < 8:
        raise HTTPException(400, "Das neue Passwort braucht mindestens 8 Zeichen.")
    if not security.verify_password(str(data.get("current") or ""), user["password_hash"]):
        raise HTTPException(400, "Das aktuelle Passwort ist falsch.")
    db.set_password(user["id"], nxt)
    return {"ok": True}


@app.post("/api/me/totp/start")
def totp_start(request: Request):
    user = request.state.user
    if user.get("totp_enabled"):
        raise HTTPException(400, "2FA ist bereits aktiv.")
    secret = security.new_totp_secret()
    db.set_totp_pending(user["id"], secret)
    return {"secret": secret, "otpauth": security.otpauth_uri(secret, user["email"])}


@app.post("/api/me/totp/confirm")
async def totp_confirm(request: Request):
    user = request.state.user
    if user.get("totp_enabled"):
        raise HTTPException(400, "2FA ist bereits aktiv.")
    secret = db.totp_pending(user["id"])
    if not secret:
        raise HTTPException(400, "Bitte 2FA zuerst einrichten.")
    data = await _json(request)
    if not security.totp_valid(secret, str(data.get("code") or "")):
        raise HTTPException(400, "Der Code ist falsch.")
    codes = security.new_recovery_codes()
    db.enable_totp(user["id"], secret, codes)
    return {"ok": True, "recovery_codes": codes}


@app.post("/api/me/totp/disable")
async def totp_disable(request: Request):
    user = db.user_by_id(request.state.user["id"])
    if not user or not user.get("totp_enabled"):
        raise HTTPException(400, "2FA ist nicht aktiv.")
    data = await _json(request)
    if not security.verify_password(str(data.get("password") or ""), user["password_hash"]):
        raise HTTPException(400, "Das Passwort ist falsch.")
    code = str(data.get("code") or "")
    if not (security.totp_valid(user.get("totp_secret") or "", code) or db.consume_recovery(user["id"], code)):
        raise HTTPException(400, "Der Code ist falsch.")
    db.disable_totp(user["id"])
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(request: Request):
    user = request.state.user
    devices = [item for item in db.list_devices() if _visible(user, item)]
    adopted = [item for item in devices if item["status"] == "adopted"]
    pending = [item for item in devices if item["status"] == "pending"]
    return {
        "adopted": len(adopted),
        "online": sum(1 for item in adopted if hub.online(item["id"])),
        "pending": len(pending),
        "tenants": len(db.list_tenants()) if user["role"] == "superadmin" else (1 if user.get("tenant_id") else 0),
        "devices": [_summary(item) for item in devices],
    }


@app.get("/api/tenants")
def tenants_list(request: Request):
    user = request.state.user
    rows = db.list_tenants()
    if user["role"] != "superadmin":
        rows = [item for item in rows if item["id"] == user.get("tenant_id")]
    return {"tenants": rows}


@app.post("/api/tenants")
async def tenants_add(request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen Mandanten anlegen.")
    data = await _json(request)
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Der Mandant braucht einen Namen.")
    return db.create_tenant(name[:120])


@app.patch("/api/tenants/{tenant_id}")
async def tenants_patch(tenant_id: str, request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen Mandanten ändern.")
    data = await _json(request)
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Der Mandant braucht einen Namen.")
    updated = db.rename_tenant(tenant_id, name[:120])
    if not updated:
        raise HTTPException(404, "Mandant nicht gefunden")
    return updated


@app.delete("/api/tenants/{tenant_id}")
def tenants_delete(tenant_id: str, request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen Mandanten löschen.")
    try:
        db.delete_tenant(tenant_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/users")
def users_list(request: Request):
    user = request.state.user
    if user["role"] == "tenant_user":
        raise HTTPException(403, "Keine Berechtigung für die Benutzerverwaltung.")
    rows = db.list_users()
    if user["role"] != "superadmin":
        rows = [item for item in rows if item["tenant_id"] == user.get("tenant_id")]
    return {"users": rows}


@app.post("/api/users")
async def users_add(request: Request):
    actor = request.state.user
    if actor["role"] == "tenant_user":
        raise HTTPException(403, "Keine Berechtigung für die Benutzerverwaltung.")
    data = await _json(request)
    email = str(data.get("email") or "").strip().lower()
    name = str(data.get("name") or "").strip()
    password = str(data.get("password") or "")
    role = str(data.get("role") or "tenant_user")
    tenant_id = data.get("tenant_id") or None
    if "@" not in email or len(email) > 180:
        raise HTTPException(400, "Die E-Mail-Adresse ist ungültig.")
    if not name:
        raise HTTPException(400, "Der Name fehlt.")
    if len(password) < 8:
        raise HTTPException(400, "Das Passwort braucht mindestens 8 Zeichen.")
    if actor["role"] == "tenant_admin":
        if role not in {"tenant_admin", "tenant_user"}:
            raise HTTPException(403, "Mandanten-Admins können nur Benutzer des eigenen Mandanten anlegen.")
        tenant_id = actor["tenant_id"]
    elif role == "superadmin":
        tenant_id = None
    elif role in {"tenant_admin", "tenant_user"}:
        if not tenant_id or not db.get_tenant(tenant_id):
            raise HTTPException(400, "Bitte einen Mandanten wählen.")
    else:
        raise HTTPException(400, "Unbekannte Rolle.")
    try:
        return db.create_user(email, name[:120], password, role, tenant_id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(400, "Diese E-Mail gibt es bereits.") from exc


@app.patch("/api/users/{user_id}")
async def users_patch(user_id: str, request: Request):
    actor = request.state.user
    if actor["role"] == "tenant_user":
        raise HTTPException(403, "Keine Berechtigung für die Benutzerverwaltung.")
    target = db.user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if actor["role"] == "tenant_admin" and target.get("tenant_id") != actor.get("tenant_id"):
        raise HTTPException(404, "Benutzer nicht gefunden")
    data = await _json(request)
    fields = {}
    if "name" in data:
        fields["name"] = str(data.get("name") or "").strip()[:120] or target["name"]
    if "email" in data:
        email = str(data.get("email") or "").strip().lower()
        if "@" not in email:
            raise HTTPException(400, "Die E-Mail-Adresse ist ungültig.")
        fields["email"] = email
    if "password" in data and data.get("password"):
        if len(str(data.get("password"))) < 8:
            raise HTTPException(400, "Das Passwort braucht mindestens 8 Zeichen.")
        fields["password"] = str(data.get("password"))
    role = str(data.get("role") or target["role"])
    tenant_id = target.get("tenant_id")
    if actor["role"] == "superadmin":
        if role == "superadmin":
            tenant_id = None
        elif role in {"tenant_admin", "tenant_user"}:
            tenant_id = data.get("tenant_id") or target.get("tenant_id")
            if not tenant_id or not db.get_tenant(tenant_id):
                raise HTTPException(400, "Bitte einen Mandanten wählen.")
        else:
            raise HTTPException(400, "Unbekannte Rolle.")
        fields["role"] = role
        fields["tenant_id"] = tenant_id
    elif "role" in data:
        if role not in {"tenant_admin", "tenant_user"}:
            raise HTTPException(403, "Diese Rolle kann nicht vergeben werden.")
        fields["role"] = role
        fields["tenant_id"] = actor["tenant_id"]
    if target["role"] == "superadmin" and fields.get("role", target["role"]) != "superadmin" and db.count_role("superadmin") <= 1:
        raise HTTPException(400, "Der letzte Cloud-Admin kann nicht geändert werden.")
    try:
        updated = db.update_user(user_id, fields)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(400, "Diese E-Mail gibt es bereits.") from exc
    return updated


@app.delete("/api/users/{user_id}")
def users_delete(user_id: str, request: Request):
    actor = request.state.user
    if actor["role"] == "tenant_user":
        raise HTTPException(403, "Keine Berechtigung für die Benutzerverwaltung.")
    if user_id == actor["id"]:
        raise HTTPException(400, "Das eigene Konto kann hier nicht gelöscht werden.")
    target = db.user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if actor["role"] == "tenant_admin" and target.get("tenant_id") != actor.get("tenant_id"):
        raise HTTPException(404, "Benutzer nicht gefunden")
    if target["role"] == "superadmin" and db.count_role("superadmin") <= 1:
        raise HTTPException(400, "Der letzte Cloud-Admin kann nicht gelöscht werden.")
    db.delete_user(user_id)
    return {"ok": True}


@app.post("/api/users/{user_id}/totp/reset")
def users_totp_reset(user_id: str, request: Request):
    actor = request.state.user
    if actor["role"] == "tenant_user":
        raise HTTPException(403, "Keine Berechtigung für die Benutzerverwaltung.")
    target = db.user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if actor["role"] == "tenant_admin" and target.get("tenant_id") != actor.get("tenant_id"):
        raise HTTPException(404, "Benutzer nicht gefunden")
    db.disable_totp(user_id)
    return {"ok": True}


@app.get("/api/keys")
def keys_list(request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins verwalten Einschreibeschlüssel.")
    return {"keys": db.list_keys()}


@app.post("/api/keys")
async def keys_add(request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins verwalten Einschreibeschlüssel.")
    data = await _json(request)
    label = str(data.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "Der Schlüssel braucht eine Bezeichnung.")
    tenant_id = data.get("tenant_id") or None
    if tenant_id and not db.get_tenant(tenant_id):
        raise HTTPException(400, "Mandant nicht gefunden")
    max_uses = data.get("max_uses")
    if max_uses in ("", None):
        max_uses = None
    else:
        try:
            max_uses = int(max_uses)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "Die maximale Anzahl ist ungültig.") from exc
        if max_uses < 1:
            raise HTTPException(400, "Die maximale Anzahl muss mindestens 1 sein.")
    return db.create_key(label[:120], tenant_id, max_uses, request.state.user["id"])


@app.post("/api/keys/{key_id}/revoke")
def keys_revoke(key_id: str, request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins verwalten Einschreibeschlüssel.")
    if not db.get_key(key_id):
        raise HTTPException(404, "Schlüssel nicht gefunden")
    db.revoke_key(key_id)
    return {"ok": True}


@app.delete("/api/keys/{key_id}")
def keys_delete(key_id: str, request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins verwalten Einschreibeschlüssel.")
    db.delete_key(key_id)
    return {"ok": True}


@app.get("/api/devices")
def devices_list(request: Request):
    user = request.state.user
    rows = [_summary(item) for item in db.list_devices() if _visible(user, item)]
    return {"devices": rows}


@app.get("/api/devices/{device_id}")
def devices_get(device_id: str, request: Request):
    device = _require_device(request.state.user, device_id)
    state, revision = db.load_state(device_id)
    return {"device": _summary(device), "state": state, "revision": revision}


@app.post("/api/devices/{device_id}/adopt")
async def devices_adopt(device_id: str, request: Request):
    user = request.state.user
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "Gerät nicht gefunden")
    if device["status"] != "pending":
        raise HTTPException(409, "Das Gerät ist bereits adoptiert.")
    data = await _json(request)
    name = str(data.get("name") or device.get("hostname") or "Signage").strip()[:120]
    if user["role"] == "superadmin":
        tenant_id = str(data.get("tenant_id") or device.get("tenant_id") or "")
        if not db.get_tenant(tenant_id):
            raise HTTPException(400, "Bitte einen Mandanten wählen.")
    elif user["role"] == "tenant_admin":
        if device.get("tenant_id") not in {None, user.get("tenant_id")}:
            raise HTTPException(404, "Gerät nicht gefunden")
        if device.get("tenant_id") and device.get("tenant_id") != user.get("tenant_id"):
            raise HTTPException(403, "Dieses Gerät gehört zu einem anderen Mandanten.")
        if not device.get("tenant_id"):
            raise HTTPException(403, "Geräte ohne Mandant adoptiert der Cloud-Admin.")
        tenant_id = user["tenant_id"]
    else:
        raise HTTPException(403, "Keine Berechtigung zum Adoptieren.")
    db.adopt_device(device_id, name, tenant_id)
    await hub.notify_adopted(device_id)
    return _summary(db.get_device(device_id))


@app.post("/api/devices/{device_id}/move")
async def devices_move(device_id: str, request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen Geräte verschieben.")
    device = _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)
    tenant_id = str(data.get("tenant_id") or "")
    if not db.get_tenant(tenant_id):
        raise HTTPException(400, "Mandant nicht gefunden")
    db.move_device(device["id"], tenant_id)
    return _summary(db.get_device(device_id))


@app.patch("/api/devices/{device_id}")
async def devices_patch(device_id: str, request: Request):
    device = _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Der Name fehlt.")
    db.rename_device(device["id"], name[:120])
    return _summary(db.get_device(device_id))


@app.delete("/api/devices/{device_id}")
async def devices_delete(device_id: str, request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen Geräte entfernen.")
    if not db.get_device(device_id):
        raise HTTPException(404, "Gerät nicht gefunden")
    db.delete_device(device_id)
    hub.devices.pop(device_id, None)
    return {"ok": True}


@app.post("/api/devices/{device_id}/queue")
async def device_queue_add(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)
    origin = data.get("origin")
    state, _revision = db.load_state(device_id)

    def add(entry: dict) -> None:
        entry["scroll_speed"] = _scroll(data.get("scroll_speed"))
        entry["zoom"] = _zoom(data.get("zoom"))
        entry["color_scheme"] = _color_scheme(data.get("color_scheme"))

        def mutator(current: dict) -> None:
            if len(current["queue"]) >= 500:
                raise HTTPException(400, "Die Warteschlange kann höchstens 500 Einträge haben.")
            entry["sort_order"] = len(current["queue"])
            entry["id"] = str(uuid.uuid4())
            entry["enabled"] = True
            current["queue"].append(entry)

        _mutate(device_id, mutator)

    if origin == "url":
        url = str(data.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or len(url) > 2000:
            raise HTTPException(400, "Die Adresse muss mit http:// oder https:// beginnen.")
        kind = _kind_from_request(data, url)
        if kind not in {"image", "video", "web"}:
            raise HTTPException(400, "Bitte den Typ Bild, Video oder Webseite wählen.")
        if kind == "web":
            fallback = urlparse(url).netloc or "Webseite"
        else:
            fallback = url.rsplit("/", 1)[-1] or "Webinhalt"
        add(
            {
                "name": (str(data.get("name") or "").strip() or fallback)[:180],
                "kind": kind,
                "origin": "url",
                "url": url,
                "file_id": None,
                "share_id": None,
                "share_path": None,
                "duration_seconds": _duration(data.get("duration_seconds"), kind),
                "fit": _fit(data, kind),
                "muted": bool(data.get("muted")),
            }
        )
    elif origin == "local":
        media = next((item for item in state["files"] if item["id"] == data.get("file_id")), None)
        if not media:
            raise HTTPException(404, "Datei nicht gefunden")
        add(
            {
                "name": (str(data.get("name") or media["name"]))[:180],
                "kind": media["kind"],
                "origin": "local",
                "url": None,
                "file_id": media["id"],
                "share_id": None,
                "share_path": None,
                "duration_seconds": _duration(data.get("duration_seconds"), media["kind"]),
                "fit": _fit(data, media["kind"]),
                "muted": bool(data.get("muted")),
            }
        )
    elif origin == "share":
        share = next((item for item in state["shares"] if item["id"] == data.get("share_id")), None)
        if not share:
            raise HTTPException(404, "Freigabe nicht gefunden")
        try:
            relative = safe_relative(str(data.get("share_path") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        kind = data.get("kind") or kind_for_name(relative)
        if not relative or kind not in {"image", "video"}:
            raise HTTPException(400, "Diese Datei kann nicht abgespielt werden.")
        add(
            {
                "name": (str(data.get("name") or Path(relative).name))[:180],
                "kind": kind,
                "origin": "share",
                "url": None,
                "file_id": None,
                "share_id": share["id"],
                "share_path": relative,
                "duration_seconds": _duration(data.get("duration_seconds"), kind),
                "fit": _fit(data, kind),
                "muted": bool(data.get("muted")),
            }
        )
    else:
        raise HTTPException(400, "Unbekannte Herkunft.")
    await _push(device_id)
    fresh, revision = db.load_state(device_id)
    return {"state": fresh, "revision": revision}


@app.patch("/api/devices/{device_id}/queue/{item_id}")
async def device_queue_patch(device_id: str, item_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)

    def mutator(state: dict) -> None:
        for item in state["queue"]:
            if item["id"] != item_id:
                continue
            if "name" in data:
                item["name"] = str(data.get("name") or item["name"])[:180]
            if "duration_seconds" in data:
                item["duration_seconds"] = _duration(data.get("duration_seconds"), item["kind"])
            if "fit" in data:
                item["fit"] = "contain" if data.get("fit") == "contain" else "cover"
            if "scroll_speed" in data:
                item["scroll_speed"] = _scroll(data.get("scroll_speed"))
            if "zoom" in data:
                item["zoom"] = _zoom(data.get("zoom"))
            if "color_scheme" in data:
                item["color_scheme"] = _color_scheme(data.get("color_scheme"))
            if "url" in data:
                if item.get("origin") != "url":
                    raise HTTPException(400, "Dieser Eintrag hat keine Adresse.")
                item["url"] = _url(data.get("url"))
            if "muted" in data:
                item["muted"] = bool(data.get("muted"))
            if "enabled" in data:
                item["enabled"] = bool(data.get("enabled"))
            return
        raise HTTPException(404, "Eintrag nicht gefunden")

    _mutate(device_id, mutator)
    await _push(device_id)
    state, revision = db.load_state(device_id)
    return {"state": state, "revision": revision}


@app.delete("/api/devices/{device_id}/queue/{item_id}")
async def device_queue_delete(device_id: str, item_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)

    def mutator(state: dict) -> None:
        state["queue"] = [item for item in state["queue"] if item["id"] != item_id]
        for index, item in enumerate(state["queue"]):
            item["sort_order"] = index

    _mutate(device_id, mutator)
    await _push(device_id)
    return {"ok": True}


@app.post("/api/devices/{device_id}/queue/order")
async def device_queue_order(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)
    ids = data.get("ids")
    if not isinstance(ids, list):
        raise HTTPException(400, "Die Reihenfolge ist ungültig.")

    def mutator(state: dict) -> None:
        current = [item["id"] for item in state["queue"]]
        if sorted(ids) != sorted(current):
            raise HTTPException(400, "Die Reihenfolge enthält nicht genau die vorhandenen Einträge.")
        by_id = {item["id"]: item for item in state["queue"]}
        state["queue"] = []
        for index, item_id in enumerate(ids):
            item = by_id[item_id]
            item["sort_order"] = index
            state["queue"].append(item)

    _mutate(device_id, mutator)
    await _push(device_id)
    state, revision = db.load_state(device_id)
    return {"state": state, "revision": revision}


@app.post("/api/devices/{device_id}/files")
async def device_file_upload(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(400, "Keine Datei erhalten.")
    original = safe_filename(getattr(upload, "filename", "") or "datei")
    kind = kind_for_name(original)
    if not kind:
        raise HTTPException(400, "Dateityp wird nicht unterstützt. Zuverlässig sind MP4, WebM und gängige Bilder.")
    file_id = str(uuid.uuid4())
    stored = file_id + Path(original).suffix.lower()
    folder = files_dir() / device_id
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / stored
    digest = hashlib.sha256()
    size = 0
    try:
        with dest.open("wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "Datei ist größer als 4 GB.")
                digest.update(chunk)
                handle.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if size <= 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Die Datei ist leer.")
    record = {
        "id": file_id,
        "name": original,
        "stored_name": stored,
        "kind": kind,
        "size": size,
        "sha256": digest.hexdigest(),
        "location": "cloud",
    }

    def mutator(state: dict) -> None:
        state["files"].append(record)

    _mutate(device_id, mutator)
    await _push(device_id)
    return record


@app.delete("/api/devices/{device_id}/files/{file_id}")
async def device_file_delete(device_id: str, file_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    removed = {}

    def mutator(state: dict) -> None:
        kept = []
        for item in state["files"]:
            if item["id"] == file_id:
                removed["item"] = item
            else:
                kept.append(item)
        if "item" not in removed:
            raise HTTPException(404, "Datei nicht gefunden")
        state["files"] = kept
        state["queue"] = [item for item in state["queue"] if item.get("file_id") != file_id]

    _mutate(device_id, mutator)
    item = removed.get("item") or {}
    if item.get("location") == "cloud":
        path = files_dir() / device_id / item.get("stored_name", "")
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    await _push(device_id)
    return {"ok": True}


@app.get("/api/devices/{device_id}/files/{file_id}/raw")
def device_file_raw(device_id: str, file_id: str, request: Request):
    _require_device(request.state.user, device_id)
    state, _revision = db.load_state(device_id)
    meta = next((item for item in state["files"] if item["id"] == file_id and item.get("location") == "cloud"), None)
    if not meta:
        raise HTTPException(404, "Datei nicht gefunden")
    path = files_dir() / device_id / meta["stored_name"]
    if not path.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.post("/api/devices/{device_id}/shares")
async def device_share_add(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)
    try:
        clean = validate_share(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    clean["id"] = str(uuid.uuid4())

    def mutator(state: dict) -> None:
        if len(state["shares"]) >= 50:
            raise HTTPException(400, "Es sind höchstens 50 Freigaben möglich.")
        state["shares"].append(clean)

    _mutate(device_id, mutator)
    await _push(device_id)
    return clean


@app.patch("/api/devices/{device_id}/shares/{share_id}")
async def device_share_patch(device_id: str, share_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)

    def mutator(state: dict) -> None:
        for item in state["shares"]:
            if item["id"] != share_id:
                continue
            merged = dict(item)
            for key in ("name", "protocol", "server", "export_path", "username", "domain", "options"):
                if key in data:
                    merged[key] = data[key]
            if data.get("password"):
                merged["password"] = str(data.get("password"))
            try:
                clean = validate_share(merged)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            item.update(clean)
            return
        raise HTTPException(404, "Freigabe nicht gefunden")

    _mutate(device_id, mutator)
    await _push(device_id)
    state, _revision = db.load_state(device_id)
    share = next(item for item in state["shares"] if item["id"] == share_id)
    return share


@app.delete("/api/devices/{device_id}/shares/{share_id}")
async def device_share_delete(device_id: str, share_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)

    def mutator(state: dict) -> None:
        state["shares"] = [item for item in state["shares"] if item["id"] != share_id]
        state["queue"] = [item for item in state["queue"] if item.get("share_id") != share_id]

    _mutate(device_id, mutator)
    await _push(device_id)
    return {"ok": True}


@app.get("/api/devices/{device_id}/shares/{share_id}/browse")
async def device_share_browse(device_id: str, share_id: str, request: Request, path: str = ""):
    _require_device(request.state.user, device_id, adopted=True)
    try:
        result = await hub.request(device_id, {"type": "browse", "share_id": share_id, "path": path}, timeout=30)
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat nicht rechtzeitig geantwortet.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/devices/{device_id}/boot-logo")
async def device_boot_logo(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(400, "Keine Datei erhalten.")
    data = await upload.read()
    if len(data) < 32 or len(data) > 1_500_000 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400, "Bitte ein PNG, höchstens 1,5 MB.")
    try:
        result = await hub.request(
            device_id,
            {"type": "boot_logo", "image": base64.b64encode(data).decode("ascii")},
            timeout=180,
        )
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat nicht rechtzeitig geantwortet.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.get("ok") is False:
        raise HTTPException(400, result.get("detail") or "Das Logo konnte nicht gespeichert werden.")
    return {"ok": True, "message": result.get("detail") or "Logo gespeichert."}


@app.delete("/api/devices/{device_id}/boot-logo")
async def device_boot_logo_clear(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    try:
        result = await hub.request(device_id, {"type": "boot_logo", "image": ""}, timeout=180)
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat nicht rechtzeitig geantwortet.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.get("ok") is False:
        raise HTTPException(400, result.get("detail") or "Das Standardlogo konnte nicht gesetzt werden.")
    return {"ok": True, "message": result.get("detail") or "Standardlogo wiederhergestellt."}


@app.get("/api/devices/{device_id}/network")
async def device_network(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    try:
        result = await hub.request(device_id, {"type": "network_get"}, timeout=20)
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat nicht rechtzeitig geantwortet.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.get("ok") is False:
        raise HTTPException(400, result.get("detail") or "Netzwerk konnte nicht gelesen werden.")
    return result


@app.post("/api/devices/{device_id}/network/scan")
async def device_network_scan(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    try:
        result = await hub.request(device_id, {"type": "network_scan"}, timeout=35)
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat nicht rechtzeitig geantwortet.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.get("ok") is False:
        raise HTTPException(400, result.get("detail") or "Die Suche hat keine Netze geliefert.")
    return result


@app.post("/api/devices/{device_id}/network")
async def device_network_apply(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)
    try:
        result = await hub.request(device_id, {"type": "network_apply", "config": data}, timeout=25)
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat nicht rechtzeitig geantwortet.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.get("ok") is False:
        raise HTTPException(400, result.get("detail") or "Netzwerk konnte nicht gespeichert werden.")
    return {"ok": True, "message": result.get("message") or "Gespeichert."}


async def _device_certs(device_id: str, message: dict, timeout: float):
    try:
        result = await hub.request(device_id, message, timeout=timeout)
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat nicht rechtzeitig geantwortet.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.get("ok") is False:
        raise HTTPException(400, result.get("detail") or "Das Zertifikat konnte nicht gespeichert werden.")
    return result


@app.get("/api/devices/{device_id}/certs")
async def device_certs(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    return await _device_certs(device_id, {"type": "certs_list"}, 12)


@app.post("/api/devices/{device_id}/certs/inspect")
async def device_certs_inspect(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)
    return await _device_certs(device_id, {"type": "certs_inspect", "host": str(data.get("host") or "")}, 20)


@app.post("/api/devices/{device_id}/certs")
async def device_certs_add(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)
    return await _device_certs(device_id, {"type": "certs_add", "pem": str(data.get("pem") or "")}, 15)


@app.delete("/api/devices/{device_id}/certs/{cert_id}")
async def device_certs_remove(device_id: str, cert_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    result = await _device_certs(device_id, {"type": "certs_remove", "id": cert_id}, 12)
    return {"ok": True, "message": result.get("message") or "Zertifikat entfernt."}


@app.post("/api/devices/{device_id}/power")
async def device_power(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)
    action = str(data.get("action") or "")
    if action not in {"reboot", "poweroff"}:
        raise HTTPException(400, "Unbekannte Aktion.")
    if action == "poweroff" and data.get("confirm") != "poweroff":
        raise HTTPException(400, "Herunterfahren braucht eine zweite Bestätigung.")
    try:
        result = await hub.request(device_id, {"type": "power", "action": action}, timeout=15)
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat nicht rechtzeitig geantwortet.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.get("ok") is False:
        raise HTTPException(400, result.get("detail") or "Aktion fehlgeschlagen.")
    return {"ok": True, "message": result.get("detail") or "Aktion gestartet."}


@app.post("/api/devices/{device_id}/update")
async def device_update(device_id: str, request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen Geräte updaten.")
    _require_device(request.state.user, device_id, adopted=True)
    try:
        result = await hub.request(device_id, {"type": "update"}, timeout=20)
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat das Update nicht bestätigt.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.get("ok") is False:
        raise HTTPException(400, result.get("detail") or "Update fehlgeschlagen.")
    return {"ok": True, "message": result.get("detail") or "Update gestartet."}


@app.post("/api/devices/{device_id}/update-schedule")
async def device_update_schedule(device_id: str, request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen Geräte updaten.")
    _require_device(request.state.user, device_id, adopted=True)
    data = await _json(request)
    job = db.schedule_job("device-update", device_id, _when(data.get("run_at")))
    return {"ok": True, "run_at": job["run_at"]}


@app.delete("/api/devices/{device_id}/update-schedule")
def device_update_schedule_clear(device_id: str, request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen Geräte updaten.")
    _require_device(request.state.user, device_id, adopted=True)
    db.cancel_job("device-update", device_id)
    return {"ok": True}


@app.get("/api/devices/{device_id}/update-log")
async def device_update_log(device_id: str, request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen Geräte updaten.")
    _require_device(request.state.user, device_id, adopted=True)
    try:
        result = await hub.request(device_id, {"type": "update_log"}, timeout=10)
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat den Update-Verlauf nicht geschickt.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return JSONResponse(
        {"log": result.get("log") or "", "state": result.get("state") or "idle"},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/devices/{device_id}/preview")
async def device_preview(device_id: str, request: Request):
    _require_device(request.state.user, device_id, adopted=True)
    try:
        result = await hub.request(device_id, {"type": "preview"}, timeout=8)
    except TimeoutError as exc:
        raise HTTPException(504, "Das Gerät hat die Anzeige nicht geschickt.") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    state = result.get("state") if isinstance(result.get("state"), dict) else {}
    return JSONResponse(
        {
            "image": result.get("image") or "",
            "state": {
                "phase": state.get("phase") or "",
                "name": state.get("name") or "",
                "kind": state.get("kind") or "",
                "detail": state.get("detail") or "",
            },
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/system")
def system_info(request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins sehen die Systemseite.")
    usage = shutil.disk_usage(data_dir())
    return {
        "version": version(),
        "commit": _git_revision(),
        "platform": sys.platform,
        "disk_free": usage.free,
        "disk_total": usage.total,
        "update_ready": updater.script_path().exists(),
        "update_at": (db.job_for("cloud-update", "") or {}).get("run_at"),
    }


@app.post("/api/system/update")
def system_update(request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen die Cloud updaten.")
    try:
        message = updater.launch()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "message": message}


@app.post("/api/system/update-schedule")
async def system_update_schedule(request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen die Cloud updaten.")
    data = await _json(request)
    job = db.schedule_job("cloud-update", "", _when(data.get("run_at")))
    return {"ok": True, "run_at": job["run_at"]}


@app.delete("/api/system/update-schedule")
def system_update_schedule_clear(request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen die Cloud updaten.")
    db.cancel_job("cloud-update", "")
    return {"ok": True}


@app.get("/api/system/update-log")
def system_update_log(request: Request):
    if request.state.user["role"] != "superadmin":
        raise HTTPException(403, "Nur Cloud-Admins dürfen die Cloud updaten.")
    return JSONResponse(updater.snapshot(), headers={"Cache-Control": "no-store"})


@app.get("/api/agent/files/{file_id}")
def agent_file(file_id: str, request: Request):
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Anmeldung erforderlich")
    token = header.split(" ", 1)[1].strip()
    device = db.device_by_token_hash(security.token_hash(token))
    if not device:
        raise HTTPException(401, "Geräte-Token ist ungültig.")
    state, _revision = db.load_state(device["id"])
    meta = next((item for item in state["files"] if item["id"] == file_id and item.get("location") == "cloud"), None)
    if not meta:
        raise HTTPException(404, "Datei nicht gefunden")
    path = files_dir() / device["id"] / meta["stored_name"]
    if not path.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.websocket("/api/agent/ws")
async def agent_ws(websocket: WebSocket):
    await websocket.accept()
    device_id = None
    try:
        hello = await asyncio.wait_for(websocket.receive_json(), 20)
        device_id = await hub.register(websocket, hello)
        if not device_id:
            await websocket.close()
            return
        while True:
            message = await websocket.receive_json()
            await hub.handle(device_id, websocket, message)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception:
        log.exception("Agent-Verbindung beendet")
    finally:
        if device_id:
            await hub.unregister(device_id, websocket)


@app.websocket("/ws/devices/{device_id}/shell")
async def device_shell(websocket: WebSocket, device_id: str):
    await websocket.accept()
    user = db.user_for_token(websocket.cookies.get(COOKIE, ""))
    device = db.get_device(device_id) if user else None
    if not user or not device or not _visible(user, device) or device["status"] != "adopted":
        await websocket.send_json({"type": "error", "detail": "Keine Berechtigung."})
        await websocket.close()
        return
    if not hub.online(device_id):
        await websocket.send_json({"type": "error", "detail": "Gerät ist offline."})
        await websocket.close()
        return
    hub.shell_clients[device_id] = websocket
    try:
        await hub.send(device_id, {"type": "shell_start", "session": "cloud", "cols": 120, "rows": 32})
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "input":
                await hub.send(device_id, {"type": "shell_input", "session": "cloud", "data": message.get("data") or ""})
            elif message.get("type") == "resize":
                await hub.send(
                    device_id,
                    {
                        "type": "shell_resize",
                        "session": "cloud",
                        "cols": int(message.get("cols") or 80),
                        "rows": int(message.get("rows") or 24),
                    },
                )
    except WebSocketDisconnect:
        pass
    finally:
        if hub.shell_clients.get(device_id) is websocket:
            hub.shell_clients.pop(device_id, None)
        await hub.send(device_id, {"type": "shell_stop", "session": "cloud"})


# Imported for side effects of the package name in logs and to keep the hub singleton shared.
_ = hub_module
