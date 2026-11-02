from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import logging
import mimetypes
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from hibit_player import agent, db, mounts, playback, power, updater, webproxy
from hibit_player.shell import shells
from hibit_player.config import data_dir, install_dir, media_dir, port, version
from hibit_player.network import hostname, local_ips
from hibit_player.security import verify_password
from hibit_player.validate import STORED_RE, kind_for_name, kind_for_url, safe_filename, safe_relative, validate_share

log = logging.getLogger("hibit.player")
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


def _playlist() -> dict:
    items = []
    for item in db.list_queue():
        if not item["enabled"]:
            continue
        src = None
        if item["origin"] == "url" and item.get("kind") in {"image", "video"} and item.get("url"):
            src = "/media/remote?u=" + quote(item["url"], safe="")
        elif item["origin"] == "url" and item.get("kind") == "web" and item.get("url"):
            src = webproxy.proxy_path(item["url"])
        elif item["origin"] == "url":
            src = item.get("url")
        elif item["origin"] == "local" and item.get("file_id"):
            media = db.get_file(item["file_id"])
            if media:
                src = f"/media/local/{media['stored_name']}"
        elif item["origin"] == "share" and item.get("share_id") and item.get("share_path"):
            src = f"/media/share/{item['share_id']}/{quote(item['share_path'])}"
        items.append(
            {
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "src": src,
                "duration_seconds": item.get("duration_seconds"),
                "fit": item.get("fit") or "cover",
                "scroll_speed": int(item.get("scroll_speed") or 0),
                "muted": bool(item.get("muted")),
                "remote": item.get("url") if item.get("origin") == "url" and item.get("kind") in {"image", "video"} else "",
            }
        )
    return {"revision": int(db.get_setting("playlist_revision") or "0"), "items": items}


def _share_view(share: dict) -> dict:
    info = mounts.status_map().get(share["id"], {})
    view = dict(share)
    view["mounted"] = bool(info.get("mounted"))
    view["error"] = info.get("error") or ""
    view["mount_path"] = info.get("mount_path") or ""
    return view


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    _created, secret = db.ensure_admin(None)
    if secret:
        print(f"HIBIT_ADMIN_CREATED admin {secret}", flush=True)
        log.warning("Initiales Admin-Passwort liegt in %s", data_dir() / "initial-password.txt")
    try:
        await asyncio.to_thread(mounts.sync_shares)
    except Exception:
        log.exception("Freigaben konnten beim Start nicht eingehängt werden")
    task = asyncio.create_task(agent.agent.run())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="HiBit Signage Player", version=version(), docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    public = path.startswith("/media/") or path.startswith("/player") or path.startswith("/console") or path in {"/", "/favicon.ico"}
    if request.method == "GET" and path in {"/api/health", "/api/display", "/api/playlist"}:
        public = True
    if request.method == "POST" and path in {"/api/login", "/api/display/report", "/api/display/frame"}:
        public = True
    if not public and path.startswith("/api/"):
        user = db.user_for_token(request.cookies.get(COOKIE, ""))
        if not user:
            return JSONResponse({"detail": "Anmeldung erforderlich"}, status_code=401)
        request.state.user = user
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _origin_ok(request):
            return JSONResponse({"detail": "Origin abgelehnt"}, status_code=403)
    return await call_next(request)


@app.get("/")
def root():
    return RedirectResponse("/console/")


@app.get("/api/health")
def health():
    return {"ok": True, "version": version(), "service": "player"}


@app.get("/api/display")
def display():
    ips = local_ips()
    state = playback.snapshot()
    return {
        "hostname": hostname(),
        "ips": ips,
        "port": port(),
        "version": version(),
        "queue_empty": not any(item["enabled"] for item in db.list_queue()),
        "watching": bool(state.get("watching")),
        "playback": {key: state.get(key) or "" for key in ("phase", "name", "kind", "detail")},
    }


@app.post("/api/display/report")
async def display_report(request: Request):
    if not _origin_ok(request):
        raise HTTPException(403, "Origin abgelehnt")
    data = await _json(request)
    phase = str(data.get("phase") or "")
    if phase not in {"empty", "loading", "playing", "failed", "blank"}:
        phase = "empty"
    playback.report(
        phase,
        str(data.get("name") or "")[:180],
        str(data.get("kind") or "")[:20],
        str(data.get("detail") or "")[:300],
    )
    return {"ok": True}


@app.post("/api/display/frame")
async def display_frame(request: Request):
    if not _origin_ok(request):
        raise HTTPException(403, "Origin abgelehnt")
    body = await request.body()
    if len(body) > 500_000 or not body.startswith(b"\xff\xd8"):
        raise HTTPException(400, "Kein Bild")
    playback.note_frame(body)
    return {"ok": True}


@app.get("/api/playlist")
def playlist():
    return _playlist()


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
    user = db.user_by_username(str(data.get("username") or "").strip())
    if not user or not verify_password(str(data.get("password") or ""), user["password_hash"]):
        recent.append(now)
        _FAILS[ip] = recent
        raise HTTPException(401, "Benutzername oder Passwort ist falsch.")
    _FAILS.pop(ip, None)
    token = db.create_session(user["id"])
    response = JSONResponse({"username": user["username"]})
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
    user = request.state.user
    return {"username": user["username"]}


@app.post("/api/me/password")
async def change_password(request: Request):
    data = await _json(request)
    user = request.state.user
    current = str(data.get("current") or "")
    nxt = str(data.get("next") or "")
    if len(nxt) < 8:
        raise HTTPException(400, "Das neue Passwort braucht mindestens 8 Zeichen.")
    if not verify_password(current, user["password_hash"]):
        raise HTTPException(400, "Das aktuelle Passwort ist falsch.")
    db.set_password(user["id"], nxt)
    return {"ok": True}


@app.get("/api/queue")
def queue_list():
    return {"items": db.list_queue(), "revision": int(db.get_setting("playlist_revision") or "0")}


@app.post("/api/queue")
async def queue_add(request: Request):
    data = await _json(request)
    origin = data.get("origin")
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
            fallback = url.rsplit("/", 1)[-1][:180] or "Webinhalt"
        name = str(data.get("name") or "").strip() or fallback
        item = {
            "name": name[:180],
            "kind": kind,
            "origin": "url",
            "url": url,
            "duration_seconds": _duration(data.get("duration_seconds"), kind),
            "fit": _fit(data, kind),
            "muted": bool(data.get("muted")),
        }
    elif origin == "local":
        media = db.get_file(str(data.get("file_id") or ""))
        if not media:
            raise HTTPException(404, "Datei nicht gefunden")
        item = {
            "name": str(data.get("name") or media["name"])[:180],
            "kind": media["kind"],
            "origin": "local",
            "file_id": media["id"],
            "duration_seconds": _duration(data.get("duration_seconds"), media["kind"]),
            "fit": _fit(data, media["kind"]),
            "muted": bool(data.get("muted")),
        }
    elif origin == "share":
        share = db.get_share(str(data.get("share_id") or ""))
        if not share:
            raise HTTPException(404, "Freigabe nicht gefunden")
        try:
            relative = safe_relative(str(data.get("share_path") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        kind = data.get("kind") or kind_for_name(relative)
        if kind not in {"image", "video"} or not relative:
            raise HTTPException(400, "Diese Datei kann nicht abgespielt werden.")
        item = {
            "name": str(data.get("name") or Path(relative).name)[:180],
            "kind": kind,
            "origin": "share",
            "share_id": share["id"],
            "share_path": relative,
            "duration_seconds": _duration(data.get("duration_seconds"), kind),
            "fit": _fit(data, kind),
            "muted": bool(data.get("muted")),
        }
    else:
        raise HTTPException(400, "Unbekannte Herkunft.")
    item["scroll_speed"] = _scroll(data.get("scroll_speed"))
    try:
        created = db.insert_queue(item)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    agent.agent.notify()
    return created


@app.patch("/api/queue/{item_id}")
async def queue_patch(item_id: str, request: Request):
    current = db.get_queue_item(item_id)
    if not current:
        raise HTTPException(404, "Eintrag nicht gefunden")
    data = await _json(request)
    fields = {}
    if "name" in data:
        fields["name"] = str(data.get("name") or "").strip()[:180] or current["name"]
    if "duration_seconds" in data:
        fields["duration_seconds"] = _duration(data.get("duration_seconds"), current["kind"])
    if "fit" in data:
        fields["fit"] = "contain" if data.get("fit") == "contain" else "cover"
    if "scroll_speed" in data:
        fields["scroll_speed"] = _scroll(data.get("scroll_speed"))
    if "muted" in data:
        fields["muted"] = bool(data.get("muted"))
    if "enabled" in data:
        fields["enabled"] = bool(data.get("enabled"))
    updated = db.update_queue(item_id, fields)
    agent.agent.notify()
    return updated


@app.delete("/api/queue/{item_id}")
def queue_delete(item_id: str):
    if not db.delete_queue(item_id):
        raise HTTPException(404, "Eintrag nicht gefunden")
    agent.agent.notify()
    return {"ok": True}


@app.post("/api/queue/order")
async def queue_order(request: Request):
    data = await _json(request)
    ids = data.get("ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise HTTPException(400, "Die Reihenfolge ist ungültig.")
    try:
        db.reorder(ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    agent.agent.notify()
    return {"items": db.list_queue()}


@app.get("/api/media")
def media_list():
    return {"files": db.list_files()}


@app.post("/api/media/upload")
async def media_upload(request: Request):
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(400, "Keine Datei erhalten.")
    original = safe_filename(getattr(upload, "filename", "") or "datei")
    kind = kind_for_name(original)
    if not kind:
        raise HTTPException(400, "Dateityp wird nicht unterstützt. Erlaubt sind gängige Bilder und Videos, zuverlässig sind MP4 und WebM.")
    file_id = str(uuid.uuid4())
    stored = file_id + Path(original).suffix.lower()
    dest = media_dir() / stored
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
    created = db.insert_file(
        {
            "id": file_id,
            "name": original,
            "stored_name": stored,
            "kind": kind,
            "size": size,
            "sha256": digest.hexdigest(),
            "location": "device",
        }
    )
    agent.agent.notify()
    return created


@app.delete("/api/media/{file_id}")
def media_delete(file_id: str):
    if not db.delete_file(file_id):
        raise HTTPException(404, "Datei nicht gefunden")
    agent.agent.notify()
    return {"ok": True}


@app.get("/media/local/{stored_name}")
def media_local(stored_name: str):
    if not STORED_RE.match(stored_name):
        raise HTTPException(404, "Datei nicht gefunden")
    path = (media_dir() / stored_name).resolve()
    if not path.is_relative_to(media_dir().resolve()) or not path.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


_MEDIA_UA = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _reject_remote(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "Die Adresse ist ungültig."
    host = parsed.hostname.lower().strip("[]")
    try:
        remote_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return "Die Adresse ist ungültig."
    if host in {"localhost", "127.0.0.1", "::1"} and remote_port == port():
        return "Diese Adresse kann der Player nicht laden."
    if host in {"169.254.169.254", "metadata.google.internal"}:
        return "Diese Adresse ist nicht erlaubt."
    return None


class _RemoteRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = _reject_remote(newurl)
        if reason:
            raise urllib.error.HTTPError(req.full_url, 403, reason, headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _remote_allowed(url: str) -> bool:
    for item in db.list_queue():
        if item.get("enabled") and item.get("origin") == "url" and item.get("kind") in {"image", "video"} and item.get("url") == url:
            return True
    return False


def _magic(prefix: bytes) -> str:
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if prefix.startswith(b"BM"):
        return "image/bmp"
    if len(prefix) >= 12 and prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    sample = prefix.lstrip()[:240].lower()
    if sample.startswith((b"<!doctype", b"<html", b"{")):
        return "text/html"
    if sample.startswith(b"<svg") or b"<svg" in sample:
        return "image/svg+xml"
    if sample.startswith(b"<"):
        return "text/html"
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        brand = prefix[8:12]
        if brand in {b"avif", b"avis"}:
            return "image/avif"
        return "video/mp4"
    if prefix.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if prefix.startswith(b"OggS"):
        return "video/ogg"
    return ""


def _range_start(header: str) -> int:
    if not header or "=" not in header:
        return 0
    try:
        start = header.split("=", 1)[1].split("-", 1)[0].strip()
        return int(start) if start else 0
    except ValueError:
        return 0


def _choose_media_type(prefix: bytes, declared: str, target: str, range_start: int) -> str | None:
    declared = (declared or "").split(";", 1)[0].strip().lower()
    sniffed = _magic(prefix) if range_start == 0 else ""
    if sniffed == "text/html":
        return None
    if sniffed.startswith(("image/", "video/")):
        if declared.startswith(("image/", "video/")):
            return declared
        return sniffed
    if declared.startswith(("image/", "video/")):
        return declared
    guessed = mimetypes.guess_type(urlparse(target).path)[0] or ""
    if guessed.startswith(("image/", "video/")):
        return guessed
    if range_start > 0:
        return declared or "application/octet-stream"
    return None


@app.api_route("/media/remote", methods=["GET", "HEAD"])
def media_remote(u: str, request: Request):
    target = u.strip()
    if not _remote_allowed(target):
        raise HTTPException(404, "Diese Adresse ist nicht in der Warteschlange.")
    reason = _reject_remote(target)
    if reason:
        raise HTTPException(400, reason)
    parsed = urlparse(target)
    headers = {
        "User-Agent": _MEDIA_UA,
        "Accept": "image/avif,image/webp,image/apng,image/*,video/mp4,video/webm,video/*,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "Referer": f"{parsed.scheme}://{parsed.netloc}/",
    }
    range_header = request.headers.get("range") or ""
    if range_header:
        headers["Range"] = range_header
    opener = urllib.request.build_opener(_RemoteRedirect)
    upstream = urllib.request.Request(target, headers=headers, method="GET")
    try:
        response = opener.open(upstream, timeout=20)
    except urllib.error.HTTPError as exc:
        log.warning("Adresse abgelehnt: %s", parsed.netloc)
        raise HTTPException(502, "Die Adresse hat den Inhalt abgelehnt.") from exc
    except Exception as exc:
        log.warning("Adresse nicht erreichbar: %s", parsed.netloc)
        raise HTTPException(502, "Die Adresse ist nicht erreichbar.") from exc
    status = getattr(response, "status", 200) or 200
    if status not in {200, 206}:
        status = 200
    declared = response.headers.get("Content-Type") or ""
    encoding = (response.headers.get("Content-Encoding") or "").lower()
    body = response
    decoded = False
    if request.method != "HEAD" and encoding in {"gzip", "x-gzip"}:
        body = gzip.GzipFile(fileobj=response)
        decoded = True
    out = {"Cache-Control": "private, max-age=60", "Accept-Ranges": "bytes"}
    if response.headers.get("Content-Range"):
        out["Content-Range"] = response.headers["Content-Range"]
    if request.method == "HEAD":
        media = _choose_media_type(b"", declared, response.geturl() or target, 0) or "application/octet-stream"
        if response.headers.get("Content-Length") and not decoded:
            out["Content-Length"] = response.headers["Content-Length"]
        response.close()
        return Response(status_code=status, media_type=media, headers=out)
    prefix = body.read(64)
    media = _choose_media_type(prefix, declared, getattr(response, "geturl", lambda: target)() or target, _range_start(range_header))
    if not media:
        response.close()
        raise HTTPException(415, "Die Adresse liefert kein Bild oder Video.")
    if response.headers.get("Content-Length") and not decoded:
        out["Content-Length"] = response.headers["Content-Length"]

    def chunks():
        try:
            if prefix:
                yield prefix
            while True:
                block = body.read(64 * 1024)
                if not block:
                    break
                yield block
        finally:
            response.close()

    return StreamingResponse(chunks(), status_code=status, media_type=media, headers=out)


@app.get("/api/shares")
def shares_list():
    return {"shares": [_share_view(item) for item in db.list_shares()]}


@app.post("/api/shares")
async def shares_add(request: Request):
    data = await _json(request)
    try:
        clean = validate_share(data)
        created = db.insert_share(clean)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    error = ""
    try:
        await asyncio.to_thread(mounts.mount_share, created)
    except RuntimeError as exc:
        error = str(exc)
    agent.agent.notify()
    view = _share_view(db.get_share(created["id"]))
    view["error"] = error or view.get("error") or ""
    return view


@app.patch("/api/shares/{share_id}")
async def shares_patch(share_id: str, request: Request):
    current = db.get_share(share_id)
    if not current:
        raise HTTPException(404, "Freigabe nicht gefunden")
    data = await _json(request)
    merged = dict(current)
    merged.update({key: data[key] for key in ("name", "protocol", "server", "export_path", "username", "domain", "options") if key in data})
    if data.get("password"):
        merged["password"] = str(data.get("password"))
    try:
        clean = validate_share(merged)
        updated = db.update_share(share_id, clean)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    error = ""
    try:
        await asyncio.to_thread(mounts.unmount_share, current)
    except RuntimeError:
        pass
    try:
        await asyncio.to_thread(mounts.mount_share, updated)
    except RuntimeError as exc:
        error = str(exc)
    agent.agent.notify()
    view = _share_view(updated)
    view["error"] = error or view.get("error") or ""
    return view


@app.delete("/api/shares/{share_id}")
async def shares_delete(share_id: str):
    current = db.get_share(share_id)
    if not current:
        raise HTTPException(404, "Freigabe nicht gefunden")
    try:
        await asyncio.to_thread(mounts.unmount_share, current)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.delete_share(share_id)
    agent.agent.notify()
    return {"ok": True}


@app.post("/api/shares/{share_id}/mount")
async def shares_mount(share_id: str):
    share = db.get_share(share_id)
    if not share:
        raise HTTPException(404, "Freigabe nicht gefunden")
    try:
        await asyncio.to_thread(mounts.mount_share, share)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _share_view(share)


@app.get("/api/shares/{share_id}/browse")
async def shares_browse(share_id: str, path: str = ""):
    result = await asyncio.to_thread(mounts.browse, share_id, path)
    if result.get("error") and not result.get("entries"):
        raise HTTPException(400, result["error"])
    return result


@app.get("/media/share/{share_id}/{file_path:path}")
def media_share(share_id: str, file_path: str):
    share = db.get_share(share_id)
    if not share:
        raise HTTPException(404, "Datei nicht gefunden")
    try:
        relative = safe_relative(file_path)
    except ValueError as exc:
        raise HTTPException(404, "Datei nicht gefunden") from exc
    root = mounts.mount_path(share)
    if not mounts.is_mounted(root):
        try:
            mounts.mount_share(share)
        except RuntimeError as exc:
            raise HTTPException(404, "Freigabe nicht verfügbar") from exc
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)


@app.get("/api/system")
def system_info():
    usage = shutil.disk_usage(data_dir())
    cloud = agent.agent.snapshot()
    cloud["device_key"] = db.get_setting("device_key") or ""
    cloud["hardware_id"] = db.hardware_id()
    return {
        "version": version(),
        "commit": _git_revision(),
        "hostname": hostname(),
        "ips": local_ips(),
        "port": port(),
        "platform": sys.platform,
        "disk_free": usage.free,
        "disk_total": usage.total,
        "cloud": cloud,
        "update_ready": updater.script_path().exists(),
    }


@app.post("/api/system/cloud")
async def system_cloud(request: Request):
    data = await _json(request)
    url = data.get("url")
    key = data.get("device_key")
    if url is not None:
        url = str(url).strip()
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(400, "Die Cloud-URL muss mit http:// oder https:// beginnen.")
    if key is not None:
        key = str(key).strip()
    db.save_cloud_config(url if url is not None else None, key if key is not None else None)
    agent.agent.request_reload()
    return {"ok": True}


@app.post("/api/system/update")
def system_update():
    try:
        message = updater.launch()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "message": message}


@app.get("/api/system/update-log")
def system_update_log():
    return JSONResponse(updater.snapshot(), headers={"Cache-Control": "no-store"})


@app.post("/api/system/power")
async def system_power(request: Request):
    data = await _json(request)
    action = str(data.get("action") or "")
    if action not in {"reboot", "poweroff"}:
        raise HTTPException(400, "Unbekannte Aktion.")
    if action == "poweroff" and data.get("confirm") != "poweroff":
        raise HTTPException(400, "Herunterfahren braucht eine zweite Bestätigung.")
    try:
        message = power.launch(action)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "message": message}


@app.post("/api/system/restart-display")
def restart_display():
    if not sys.platform.startswith("linux"):
        raise HTTPException(400, "Die Anzeige gibt es auf diesem Rechner nicht.")
    try:
        proc = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "hibit-kiosk"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "Neustart der Anzeige fehlgeschlagen.").strip()
        raise HTTPException(400, detail)
    return {"ok": True}


@app.websocket("/ws/shell")
async def shell_socket(ws: WebSocket):
    await ws.accept()
    user = db.user_for_token(ws.cookies.get(COOKIE, ""))
    if not user:
        await ws.send_text('{"type":"error","detail":"Anmeldung erforderlich"}')
        await ws.close()
        return
    loop = asyncio.get_running_loop()

    def on_bytes(data: bytes) -> None:
        packet = '{"type":"data","data":"' + base64.b64encode(data).decode("ascii") + '"}'
        asyncio.run_coroutine_threadsafe(ws.send_text(packet), loop)

    try:
        await shells.open(on_bytes, 120, 32)
        on_bytes("HiBit Signage Shell\r\n".encode())
        while True:
            message = await ws.receive_json()
            if message.get("type") == "input":
                shells.write(base64.b64decode(message.get("data") or ""))
            elif message.get("type") == "resize":
                shells.resize(int(message.get("cols") or 80), int(message.get("rows") or 24))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        with suppress(Exception):
            await ws.send_text('{"type":"error","detail":' + '"' + str(exc).replace('"', "'") + '"' + "}")
    finally:
        await shells.close()


@app.get("/player")
@app.get("/player/")
def player_page():
    return _html(STATIC / "player" / "index.html")


@app.get("/player/player.js")
def player_js():
    return _static(STATIC / "player" / "player.js", "text/javascript")


@app.get("/player/player.css")
def player_css():
    return _static(STATIC / "player" / "player.css", "text/css")


@app.get("/player/sw.js")
def player_sw():
    path = STATIC / "player" / "sw.js"
    if not path.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(
        path,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.api_route("/player/site/{scheme}/{host}/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def player_site(scheme: str, host: str, path: str, request: Request):
    return await webproxy.handle(scheme, host, path, request)


@app.api_route("/player/site/{scheme}/{host}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def player_site_root(scheme: str, host: str, request: Request):
    return await webproxy.handle(scheme, host, "", request)


@app.websocket("/player/site/{scheme}/{host}/{path:path}")
async def player_site_ws(ws: WebSocket, scheme: str, host: str, path: str):
    await webproxy.handle_ws(ws, scheme, host, path)


@app.websocket("/player/site/{scheme}/{host}")
async def player_site_ws_root(ws: WebSocket, scheme: str, host: str):
    await webproxy.handle_ws(ws, scheme, host, "")


@app.get("/console")
@app.get("/console/")
def console_page():
    return _html(STATIC / "console" / "index.html")


@app.get("/console/console.js")
def console_js():
    return _static(STATIC / "console" / "console.js", "text/javascript")


@app.get("/console/console.css")
def console_css():
    return _static(STATIC / "console" / "console.css", "text/css")


@app.get("/console/terminal.js")
def console_terminal():
    return _static(SHARED / "terminal.js", "text/javascript")
