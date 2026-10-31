from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from hibit_cloud import db
from hibit_cloud.config import port


def main() -> None:
    parser = argparse.ArgumentParser(prog="hibit-cloud", description="HiBit Signage Cloud")
    parser.add_argument("--init-only", action="store_true")
    parser.add_argument("--admin-email", default=os.environ.get("HIBIT_ADMIN_EMAIL"))
    parser.add_argument("--admin-password", default=os.environ.get("HIBIT_ADMIN_PASSWORD"))
    parser.add_argument("--admin-name", default=os.environ.get("HIBIT_ADMIN_NAME") or "Cloud-Admin")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init()
    created, secret = db.ensure_admin(args.admin_email, args.admin_password, args.admin_name)
    mail = (args.admin_email or "admin@hibit.local").strip().lower()
    if created and secret:
        print(f"HIBIT_ADMIN_CREATED {mail} {secret}", flush=True)
    elif created:
        print(f"HIBIT_ADMIN_CREATED {mail}", flush=True)
    elif args.init_only:
        print("HIBIT_ADMIN_EXISTS", flush=True)
    if args.init_only:
        return
    uvicorn.run(
        "hibit_cloud.server:app",
        host=args.host,
        port=args.port or port(),
        log_level="info",
        ws_max_size=8_000_000,
    )


if __name__ == "__main__":
    main()
