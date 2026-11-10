from __future__ import annotations

import asyncio
import json
import logging
import uuid

from hibit_cloud import db, security, updater
from hibit_cloud.config import files_dir
from hibit_cloud.validate import sanitize_state

log = logging.getLogger("hibit.cloud.hub")


def _whole(value) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 10 * 365 * 24 * 3600:
        return None
    return number


def runtime_from(msg: dict) -> dict:
    load = str(msg.get("load") or "").strip()[:16]
    if load and not load.replace(".", "", 1).isdigit():
        load = ""
    return {
        "mounts": msg.get("mounts") or {},
        "last_error": str(msg.get("last_error") or "")[:500],
        "applied_revision": msg.get("applied_revision") or 0,
        "dirty": bool(msg.get("dirty")),
        "uptime_seconds": _whole(msg.get("uptime_seconds")),
        "service_seconds": _whole(msg.get("service_seconds")),
        "load": load,
        "mem_total": _whole(msg.get("mem_total")),
        "mem_available": _whole(msg.get("mem_available")),
    }


def _empty(state: dict) -> bool:
    return not state.get("queue") and not state.get("shares") and not state.get("files")


def _merge_device_state(device_id: str, incoming: dict) -> dict:
    """Keep cloud-file metadata authoritative and delete blobs the device dropped."""
    current, _revision = db.load_state(device_id)
    clean = sanitize_state(incoming)
    old_cloud = {item["id"]: item for item in current["files"] if item.get("location") == "cloud"}
    merged_files = []
    seen: set[str] = set()
    for item in clean["files"]:
        if item["id"] in old_cloud:
            merged_files.append(old_cloud[item["id"]])
        elif item.get("location") != "cloud":
            merged_files.append(item)
        seen.add(item["id"])
    for item in current["files"]:
        if item.get("location") == "cloud" and item["id"] not in seen:
            path = files_dir() / device_id / item["stored_name"]
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    clean["files"] = merged_files
    return clean


def decide_sync(device: dict, applied: int, dirty: bool, state: dict | None) -> dict | None:
    """Return the next sync message for an adopted device, or None when nothing is due.

    The cloud revision wins when both sides changed. A device can seed the cloud
    only while the cloud has never stored a playlist.
    """
    if device.get("status") != "adopted":
        return None
    cloud_rev = int(device.get("state_revision") or 0)
    if cloud_rev == 0 and applied == 0:
        if state and not _empty(state):
            saved = _merge_device_state(device["id"], state)
            db.save_state(device["id"], saved, 1)
            return {"type": "sync", "action": "accepted", "revision": 1}
        if state is None:
            return {"type": "sync_request"}
        return None
    if dirty and applied == cloud_rev and state is not None:
        saved = _merge_device_state(device["id"], state)
        revision = cloud_rev + 1
        db.save_state(device["id"], saved, revision)
        return {"type": "sync", "action": "accepted", "revision": revision}
    if cloud_rev > applied:
        cloud_state, revision = db.load_state(device["id"])
        return {"type": "sync", "action": "apply", "revision": revision, "state": cloud_state}
    if dirty and state is None:
        return {"type": "sync_request"}
    return None


class Hub:
    def __init__(self) -> None:
        self.devices: dict[str, object] = {}
        self.shell_clients: dict[str, object] = {}
        self.pending: dict[str, asyncio.Future] = {}

    def online(self, device_id: str) -> bool:
        return device_id in self.devices

    async def send(self, device_id: str, message: dict) -> bool:
        ws = self.devices.get(device_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(message))
            return True
        except Exception:
            return False

    async def push_state(self, device_id: str) -> bool:
        state, revision = db.load_state(device_id)
        return await self.send(
            device_id,
            {"type": "sync", "action": "apply", "revision": revision, "state": state},
        )

    async def notify_adopted(self, device_id: str) -> None:
        device = db.get_device(device_id)
        if not device:
            return
        await self.send(
            device_id,
            {
                "type": "welcome",
                "status": "adopted",
                "device_id": device_id,
                "name": device.get("name") or "",
                "tenant_name": device.get("tenant_name") or "",
                "revision": int(device.get("state_revision") or 0),
            },
        )
        await self.send(device_id, {"type": "sync_request"})

    async def dispatch_updates(self, only_device: str | None = None) -> None:
        for job in db.due_jobs():
            kind = job.get("kind")
            target = job.get("target_id") or ""
            if kind == "device-update":
                if only_device and target != only_device:
                    continue
                if not self.online(target):
                    continue
                if not db.lease_job(job["id"]):
                    continue
                try:
                    await self.request(target, {"type": "update"}, timeout=20)
                except Exception as exc:
                    log.warning("Geplantes Update für %s: %s", target, exc)
                    db.release_job(job["id"])
                    continue
                db.delete_job(job["id"])
            elif kind == "cloud-update" and only_device is None:
                if not db.lease_job(job["id"]):
                    continue
                try:
                    updater.launch()
                except Exception as exc:
                    log.warning("Geplantes Cloud-Update: %s", exc)
                    db.release_job(job["id"])
                    continue
                db.delete_job(job["id"])

    async def request(self, device_id: str, message: dict, timeout: float = 25) -> dict:
        if device_id not in self.devices:
            raise RuntimeError("Gerät ist offline.")
        req = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[req] = future
        payload = dict(message)
        payload["req"] = req
        try:
            if not await self.send(device_id, payload):
                raise RuntimeError("Gerät ist offline.")
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(req, None)

    async def register(self, ws, hello: dict) -> str | None:
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            await ws.send_text(json.dumps({"type": "error", "code": "unauthorized", "detail": "Anmeldung fehlt."}))
            return None
        hardware_id = str(hello.get("hardware_id") or "").strip()
        if not hardware_id or len(hardware_id) > 128:
            await ws.send_text(json.dumps({"type": "error", "code": "unauthorized", "detail": "Hardware-ID fehlt."}))
            return None
        token = str(hello.get("token") or "")
        device = db.device_by_token_hash(security.token_hash(token)) if token else None
        if device and device.get("hardware_id") != hardware_id:
            device = None
        issued = None
        if device is None and token and not hello.get("device_key"):
            await ws.send_text(
                json.dumps({"type": "error", "code": "bad_token", "detail": "Geräte-Token ist ungültig."})
            )
            return None
        if device is None:
            key_row = db.key_by_secret(str(hello.get("device_key") or ""))
            existing = db.device_by_hardware(hardware_id)
            if existing:
                if not key_row or key_row.get("revoked"):
                    await ws.send_text(
                        json.dumps({"type": "error", "code": "bad_key", "detail": "Der Einschreibeschlüssel ist ungültig."})
                    )
                    return None
                issued = security.new_token()
                db.rotate_token(existing["id"], issued)
                device = db.get_device(existing["id"])
            else:
                problem = db.key_can_enroll(key_row) if key_row else "Der Einschreibeschlüssel ist ungültig."
                if problem or not key_row:
                    await ws.send_text(json.dumps({"type": "error", "code": "bad_key", "detail": problem or "Ungültig."}))
                    return None
                issued = security.new_token()
                device = db.insert_device(
                    hardware_id,
                    str(hello.get("hostname") or ""),
                    str(hello.get("version") or ""),
                    list(hello.get("ips") or []),
                    key_row,
                    issued,
                )
        if not device:
            return None
        runtime = runtime_from(hello)
        db.touch_device(
            device["id"],
            str(hello.get("hostname") or device.get("hostname") or ""),
            str(hello.get("version") or ""),
            list(hello.get("ips") or []),
            runtime,
        )
        device = db.get_device(device["id"])
        self.devices[device["id"]] = ws
        welcome = {
            "type": "welcome",
            "status": device["status"],
            "device_id": device["id"],
            "name": device.get("name") or "",
            "tenant_name": device.get("tenant_name") or "",
            "revision": int(device.get("state_revision") or 0),
        }
        if issued:
            welcome["device_token"] = issued
        await ws.send_text(json.dumps(welcome))
        action = decide_sync(
            device,
            int(hello.get("applied_revision") or 0),
            bool(hello.get("dirty")),
            hello.get("state") if isinstance(hello.get("state"), dict) else None,
        )
        if action:
            await ws.send_text(json.dumps(action))
        log.info("Gerät %s verbunden (%s)", device["id"], device["status"])
        return device["id"]

    async def unregister(self, device_id: str, ws) -> None:
        if self.devices.get(device_id) is ws:
            self.devices.pop(device_id, None)
            log.info("Gerät %s getrennt", device_id)

    async def handle(self, device_id: str, ws, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "report":
            device = db.get_device(device_id)
            if not device:
                return
            db.touch_device(
                device_id,
                str(msg.get("hostname") or device.get("hostname") or ""),
                device.get("version") or "",
                device.get("ips") or [],
            )
            device = db.get_device(device_id)
            action = decide_sync(
                device,
                int(msg.get("base_revision") or 0),
                bool(msg.get("dirty")),
                msg.get("state") if isinstance(msg.get("state"), dict) else None,
            )
            if action:
                await ws.send_text(json.dumps(action))
            return
        if kind == "heartbeat":
            device = db.get_device(device_id)
            if not device:
                return
            runtime = runtime_from(msg)
            db.touch_device(
                device_id,
                str(msg.get("hostname") or device.get("hostname") or ""),
                str(msg.get("version") or device.get("version") or ""),
                list(msg.get("ips") or []),
                runtime,
            )
            asyncio.create_task(self.dispatch_updates(device_id))
            applied = int(msg.get("applied_revision") or 0)
            fresh = db.get_device(device_id)
            if fresh and fresh["status"] == "adopted" and int(fresh["state_revision"] or 0) > applied:
                await self.push_state(device_id)
            return
        if kind in {"browse_result", "update_started", "power_done", "update_log", "preview", "boot_logo_done", "network_done"}:
            future = self.pending.get(str(msg.get("req") or ""))
            if future and not future.done():
                future.set_result(msg)
            return
        if kind == "shell_data":
            admin = self.shell_clients.get(device_id)
            if admin is not None:
                try:
                    await admin.send_text(json.dumps({"type": "data", "data": msg.get("data") or ""}))
                except Exception:
                    self.shell_clients.pop(device_id, None)
            return
        if kind == "shell_exit":
            admin = self.shell_clients.get(device_id)
            if admin is not None:
                try:
                    await admin.send_text(json.dumps({"type": "exit", "detail": msg.get("detail") or ""}))
                except Exception:
                    pass
            return


hub = Hub()
