from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from hibit_player import db
from hibit_player.config import port


def main() -> None:
    parser = argparse.ArgumentParser(prog="hibit-player", description="HiBit Signage Player")
    parser.add_argument("--init-only", action="store_true", help="Konfiguration schreiben und beenden")
    parser.add_argument("--cloud-url", default=None)
    parser.add_argument("--device-key", default=None)
    parser.add_argument("--admin-password", default=os.environ.get("HIBIT_ADMIN_PASSWORD"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init()
    created, secret = db.ensure_admin(args.admin_password)
    if args.cloud_url is not None or args.device_key is not None:
        db.save_cloud_config(args.cloud_url, args.device_key)
    if created and secret:
        print(f"HIBIT_ADMIN_CREATED admin {secret}", flush=True)
    elif created:
        print("HIBIT_ADMIN_CREATED admin", flush=True)
    elif args.init_only:
        print("HIBIT_ADMIN_EXISTS", flush=True)
    if args.init_only:
        return
    uvicorn.run(
        "hibit_player.server:app",
        host=args.host,
        port=args.port or port(),
        log_level="info",
        ws_max_size=8_000_000,
    )


if __name__ == "__main__":
    main()
