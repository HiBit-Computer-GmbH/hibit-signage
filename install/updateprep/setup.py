from __future__ import annotations

import os
import subprocess

from setuptools import setup

NOTE = "[Service]\nKillMode=process\n"


def protect() -> None:
    if os.geteuid() != 0:
        return
    changed = False
    for unit in ("hibit-cloud.service", "hibit-signage.service"):
        if not os.path.isfile(f"/etc/systemd/system/{unit}"):
            continue
        folder = f"/etc/systemd/system/{unit}.d"
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, "hibit-update.conf")
        current = ""
        if os.path.isfile(target):
            with open(target, encoding="utf-8") as handle:
                current = handle.read()
        if current != NOTE:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(NOTE)
            changed = True
    if changed:
        systemctl = "/usr/bin/systemctl" if os.path.isfile("/usr/bin/systemctl") else "systemctl"
        subprocess.run([systemctl, "daemon-reload"], check=False)


protect()

setup(
    name="hibit-update-prep",
    version="1.1.9",
    description="Hält das HiBit-Update über den Dienstneustart am Leben.",
    py_modules=["hibit_update_prep"],
    python_requires=">=3.11",
)
