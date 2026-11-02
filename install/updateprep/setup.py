from __future__ import annotations

import subprocess
from pathlib import Path

from setuptools import setup

script = Path(__file__).resolve().parent / "hibit-update-protect.sh"
print("hibit-update-prep: schütze den Dienstneustart", flush=True)
if script.is_file():
    subprocess.run(["/bin/bash", str(script)], check=False)

setup(
    name="hibit-update-prep",
    version="1.1.8",
    description="Hält das HiBit-Update über den Dienstneustart am Leben.",
    py_modules=["hibit_update_prep"],
    python_requires=">=3.11",
)
