from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid

from hibit_player import db, mounts, playback, power, screen, splash, updater
from hibit_player.shell import shells
from hibit_player.config import media_dir, version
from hibit_player.network import hostname, local_ips, network_apply, network_scan, network_status

log = logging.getLogger("hibit.player.agent")
_STARTED = time.monotonic()


def runtime_facts() -> dict:
    facts = {"service_seconds": int(time.monotonic() - _STARTED)}
    try:
        with open("/proc/uptime", encoding="ascii") as handle:
            facts["uptime_seconds"] = int(float(handle.read().split()[0]))
    except OSError:
        pass
    try:
        with open("/proc/loadavg", encoding="ascii") as handle:
            facts["load"] = handle.read().split()[0]
    except OSError:
        pass
    try:
        memory = {}
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in {"MemTotal", "MemAvailable"}:
                    memory[key] = int(rest.strip().split()[0]) * 1024
        if "MemTotal" in memory:
            facts["mem_total"] = memory["MemTotal"]
            facts["mem_available"] = memory.get("MemAvailable")
    except OSError:
        pass
    return facts


def ws_url(cloud_url: str) -> str:
    base = cloud_url.strip().rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :] + "/api/agent/ws"
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :] + "/api/agent/ws"
    raise ValueError("Die Cloud-URL muss mit http:// oder https:// beginnen.")


class CloudAgent:
    def __init__(self) -> None:
        self.status = "disabled"
        self.last_error = ""
        self.connected = False
        self.wake: asyncio.Event | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._state_lock = asyncio.Lock()
        self._pushing = False
        self.reload = False
        self.mount_errors: dict[str, str] = {}

    def notify(self) -> None:
        if self.loop and self.wake:
            self.loop.call_soon_threadsafe(self.wake.set)

    def request_reload(self) -> None:
        self.reload = True
        self.notify()

    def snapshot(self) -> dict:
        return {
            "status": db.get_setting("cloud_status") or self.status,
            "connected": self.connected,
            "url": db.get_setting("cloud_url") or "",
            "last_error": self.last_error,
            "name": db.get_setting("cloud_name") or "",
            "tenant": db.get_setting("tenant_name") or "",
            "revision": int(db.get_setting("applied_revision") or "0"),
            "dirty": db.get_setting("dirty") == "1",
        }

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        delay = 2
        while True:
            cloud_url = (db.get_setting("cloud_url") or "").strip()
            token = db.get_setting("device_token") or ""
            key = db.get_setting("device_key") or ""
            if not cloud_url or not (token or key):
                self.status = "disabled"
                self.connected = False
                db.set_setting("cloud_status", "disabled")
                await asyncio.sleep(3)
                continue
            try:
                self.status = "connecting"
                db.set_setting("cloud_status", "connecting")
                await self._session(cloud_url, token, key)
                delay = 2
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.status = "offline"
                db.set_setting("cloud_status", "offline")
                self.last_error = str(exc)
                log.warning("Cloud-Verbindung getrennt: %s", exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _session(self, cloud_url: str, token: str, key: str) -> None:
        import websockets

        url = ws_url(cloud_url)
        async with websockets.connect(url, open_timeout=15, ping_interval=20, ping_timeout=20, max_size=8_000_000) as ws:
            self._ws = ws
            hello = self._hello(token, key)
            await ws.send(json.dumps(hello))
            reader = asyncio.create_task(self._reader(ws))
            beater = asyncio.create_task(self._heartbeats(ws))
            pusher = asyncio.create_task(self._pusher(ws))
            done, pending = await asyncio.wait({reader, beater, pusher}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
        self._ws = None
        self.connected = False

    def _hello(self, token: str, key: str) -> dict:
        meta = db.sync_meta()
        include_state = meta["dirty"] or meta["applied_revision"] == 0
        payload = {
            "type": "hello",
            "hardware_id": db.hardware_id(),
            "hostname": hostname(),
            "version": version(),
            "ips": local_ips(),
            "applied_revision": meta["applied_revision"],
            "dirty": meta["dirty"],
            "mounts": mounts.status_map(),
            **runtime_facts(),
        }
        if token:
            payload["token"] = token
        elif key:
            payload["device_key"] = key
        if include_state:
            payload["state"] = db.export_state()
        return payload

    async def _reader(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "error":
                if msg.get("code") == "bad_token":
                    db.set_setting("device_token", "")
                raise RuntimeError(msg.get("detail") or "Cloud hat die Anmeldung abgelehnt.")
            if kind == "welcome":
                self._apply_welcome(msg)
            elif kind == "sync":
                await self._on_sync(msg)
            elif kind == "sync_request":
                await self._send_report(ws, force=True)
            elif kind == "shell_start":
                await self._shell_start(ws, msg)
            elif kind == "shell_input":
                data = base64.b64decode(msg.get("data") or "")
                shells.write(data)
            elif kind == "shell_resize":
                shells.resize(int(msg.get("cols") or 80), int(msg.get("rows") or 24))
            elif kind == "shell_stop":
                await shells.close()
            elif kind == "browse":
                result = await asyncio.to_thread(mounts.browse, msg.get("share_id") or "", msg.get("path") or "")
                await ws.send(json.dumps({"type": "browse_result", "req": msg.get("req"), **result}))
            elif kind == "update":
                try:
                    message = updater.launch()
                    await ws.send(json.dumps({"type": "update_started", "req": msg.get("req"), "detail": message}))
                except Exception as exc:
                    await ws.send(json.dumps({"type": "update_started", "req": msg.get("req"), "detail": str(exc), "ok": False}))
            elif kind == "update_log":
                snap = updater.snapshot()
                await ws.send(json.dumps({"type": "update_log", "req": msg.get("req"), "log": snap["log"], "state": snap["state"]}))
            elif kind == "preview":
                playback.arm_watch()
                image = await screen.capture_jpeg()
                if not image:
                    image = playback.frame_if_fresh()
                payload = {
                    "type": "preview",
                    "req": msg.get("req"),
                    "image": base64.b64encode(image).decode("ascii") if image else "",
                    "state": playback.snapshot(),
                }
                await ws.send(json.dumps(payload))
            elif kind == "boot_logo":
                try:
                    raw = base64.b64decode(msg.get("image") or "", validate=False)
                    if raw:
                        splash.save_logo(raw)
                    else:
                        splash.clear_logo()
                    message = await asyncio.to_thread(splash.apply)
                    await ws.send(json.dumps({"type": "boot_logo_done", "req": msg.get("req"), "ok": True, "detail": message}))
                except Exception as exc:
                    await ws.send(json.dumps({"type": "boot_logo_done", "req": msg.get("req"), "ok": False, "detail": str(exc)}))
            elif kind == "power":
                try:
                    message = power.launch(str(msg.get("action") or ""))
                    await ws.send(json.dumps({"type": "power_done", "req": msg.get("req"), "detail": message, "ok": True}))
                except Exception as exc:
                    await ws.send(json.dumps({"type": "power_done", "req": msg.get("req"), "detail": str(exc), "ok": False}))
            elif kind in {"network_get", "network_scan", "network_apply"}:
                await self._network(ws, msg)

    async def _network(self, ws, msg: dict) -> None:
        kind = msg.get("type")
        try:
            if kind == "network_get":
                payload = await asyncio.to_thread(network_status)
                await ws.send(json.dumps({"type": "network_done", "req": msg.get("req"), "ok": True, **payload}))
            elif kind == "network_scan":
                payload = await asyncio.to_thread(network_scan)
                await ws.send(json.dumps({"type": "network_done", "req": msg.get("req"), "ok": True, **payload}))
            else:
                message = await asyncio.to_thread(network_apply, msg.get("config") if isinstance(msg.get("config"), dict) else {})
                await ws.send(json.dumps({"type": "network_done", "req": msg.get("req"), "ok": True, "message": message}))
        except Exception as exc:
            await ws.send(json.dumps({"type": "network_done", "req": msg.get("req"), "ok": False, "detail": str(exc)}))

    def _apply_welcome(self, msg: dict) -> None:
        status = msg.get("status") or "pending"
        self.status = status
        self.connected = True
        self.last_error = ""
        db.set_setting("cloud_status", status)
        if msg.get("device_id"):
            db.set_setting("device_id", msg["device_id"])
        if msg.get("device_token"):
            db.set_setting("device_token", msg["device_token"])
        db.set_setting("cloud_name", msg.get("name") or "")
        db.set_setting("tenant_name", msg.get("tenant_name") or "")
        log.info("Cloud-Status: %s", status)

    async def _on_sync(self, msg: dict) -> None:
        action = msg.get("action")
        revision = int(msg.get("revision") or 0)
        if action == "accepted":
            db.set_setting("applied_revision", str(revision))
            db.set_setting("dirty", "0")
            self.last_error = ""
            return
        if action == "apply":
            await self._apply_state(msg.get("state") or {}, revision)

    async def _apply_state(self, state: dict, revision: int) -> None:
        async with self._state_lock:
            current = int(db.get_setting("applied_revision") or "0")
            if revision <= current:
                return
            try:
                await self._download_files(state.get("files") or [])
                db.replace_state(state, revision)
                self.mount_errors = await asyncio.to_thread(mounts.sync_shares, state.get("shares") or [])
                self.last_error = ""
                log.info("Cloud-Zustand %s übernommen", revision)
            except Exception as exc:
                self.last_error = str(exc)
                log.exception("Cloud-Zustand konnte nicht übernommen werden")
        if self._ws:
            try:
                await self._ws.send(json.dumps(self._heartbeat()))
            except Exception:
                pass

    async def _download_files(self, files: list[dict]) -> None:
        cloud_url = (db.get_setting("cloud_url") or "").rstrip("/")
        token = db.get_setting("device_token") or ""
        media_dir().mkdir(parents=True, exist_ok=True)
        for item in files:
            if item.get("location") != "cloud":
                continue
            dest = media_dir() / item["stored_name"]
            expected = (item.get("sha256") or "").lower()
            if dest.exists() and expected and _sha256_file(dest) == expected:
                continue
            if not token:
                raise RuntimeError("Dateidownload ohne Geräte-Token nicht möglich.")
            url = f"{cloud_url}/api/agent/files/{item['id']}"
            tmp = dest.with_suffix(dest.suffix + ".part")
            await asyncio.to_thread(_download, url, token, tmp, expected)
            os.replace(tmp, dest)

    async def _heartbeats(self, ws) -> None:
        while True:
            await asyncio.sleep(15)
            await ws.send(json.dumps(self._heartbeat()))

    def _heartbeat(self) -> dict:
        meta = db.sync_meta()
        return {
            "type": "heartbeat",
            "ips": local_ips(),
            "version": version(),
            "hostname": hostname(),
            "applied_revision": meta["applied_revision"],
            "dirty": meta["dirty"],
            "mounts": mounts.status_map(),
            "last_error": self.last_error,
            **runtime_facts(),
        }

    async def _pusher(self, ws) -> None:
        while True:
            assert self.wake
            await self.wake.wait()
            self.wake.clear()
            if self.reload:
                self.reload = False
                await ws.close()
                return
            await self._send_report(ws, force=False)

    async def _send_report(self, ws, force: bool) -> None:
        async with self._state_lock:
            if self._pushing:
                return
            meta = db.sync_meta()
            if not force and not meta["dirty"]:
                return
            if db.get_setting("cloud_status") != "adopted" and not force:
                return
            payload = {
                "type": "report",
                "req": str(uuid.uuid4()),
                "base_revision": meta["applied_revision"],
                "dirty": True if force else meta["dirty"],
                "state": db.export_state(),
            }
            self._pushing = True
        try:
            await ws.send(json.dumps(payload))
        finally:
            self._pushing = False

    async def _shell_start(self, ws, msg: dict) -> None:
        session = msg.get("session") or "cloud"

        def on_bytes(data: bytes) -> None:
            packet = {
                "type": "shell_data",
                "session": session,
                "data": base64.b64encode(data).decode("ascii"),
            }
            if self.loop and self._ws:
                asyncio.run_coroutine_threadsafe(self._send_shell(packet), self.loop)

        try:
            await shells.open(on_bytes, int(msg.get("cols") or 120), int(msg.get("rows") or 32))
            on_bytes("HiBit Signage Shell\r\n".encode())
        except Exception as exc:
            await ws.send(json.dumps({"type": "shell_exit", "session": session, "detail": str(exc)}))

    async def _send_shell(self, packet: dict) -> None:
        if not self._ws:
            return
        try:
            await self._ws.send(json.dumps(packet))
        except Exception:
            pass


def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, token: str, dest, expected: str) -> None:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": f"HiBitSignage/{version()}"})
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=3600) as response, open(dest, "wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                handle.write(chunk)
    except urllib.error.HTTPError as exc:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"Datei konnte nicht geladen werden ({exc.code}).") from exc
    except Exception:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if expected and digest.hexdigest() != expected.lower():
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("Die Prüfsumme einer Cloud-Datei stimmt nicht.")


agent = CloudAgent()
