from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys

from hibit_player.config import data_dir


class ShellSession:
    def __init__(self) -> None:
        self.master: int | None = None
        self.proc: asyncio.subprocess.Process | subprocess.Popen | None = None
        self._reader_task: asyncio.Task | None = None
        self._closed = False

    async def start(self, on_bytes, cols: int = 120, rows: int = 32) -> None:
        if sys.platform.startswith("linux"):
            await self._start_pty(on_bytes, cols, rows)
        else:
            await self._start_pipe(on_bytes)

    async def _start_pty(self, on_bytes, cols: int, rows: int) -> None:
        import fcntl
        import pty
        import struct
        import termios

        master, slave = pty.openpty()
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        env.setdefault("LANG", "C.UTF-8")
        self.proc = await asyncio.create_subprocess_exec(
            "/bin/bash",
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            cwd=str(data_dir()),
            start_new_session=True,
        )
        os.close(slave)
        self.master = master
        loop = asyncio.get_running_loop()

        def _read() -> None:
            try:
                data = os.read(master, 65536)
            except OSError:
                data = b""
            if data:
                on_bytes(data)
                return
            try:
                loop.remove_reader(master)
            except Exception:
                pass

        loop.add_reader(master, _read)
        self._loop = loop

    async def _start_pipe(self, on_bytes) -> None:
        args = ["cmd.exe", "/Q"] if os.name == "nt" else ["/bin/sh"]
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(data_dir()),
            bufsize=0,
            **flags,
        )
        loop = asyncio.get_running_loop()

        def _pump() -> None:
            assert self.proc and self.proc.stdout
            while True:
                chunk = self.proc.stdout.read(4096)
                if not chunk:
                    break
                loop.call_soon_threadsafe(on_bytes, chunk)

        self._reader_task = asyncio.create_task(asyncio.to_thread(_pump))

    def write(self, data: bytes) -> None:
        if not data or self._closed:
            return
        if self.master is not None:
            try:
                os.write(self.master, data)
            except OSError:
                pass
            return
        if os.name == "nt":
            data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        proc = self.proc
        if isinstance(proc, subprocess.Popen) and proc.stdin:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except OSError:
                pass

    def resize(self, cols: int, rows: int) -> None:
        if self.master is None:
            return
        import fcntl
        import struct
        import termios

        try:
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0))
        except OSError:
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.master is not None:
            try:
                asyncio.get_running_loop().remove_reader(self.master)
            except Exception:
                pass
            try:
                os.close(self.master)
            except OSError:
                pass
            self.master = None
        proc = self.proc
        if isinstance(proc, asyncio.subprocess.Process):
            if proc.returncode is None and proc.pid:
                try:
                    os.killpg(proc.pid, signal.SIGHUP)
                except OSError:
                    proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except (asyncio.TimeoutError, ProcessLookupError):
                proc.kill()
        elif isinstance(proc, subprocess.Popen):
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass


class ShellManager:
    def __init__(self) -> None:
        self.session: ShellSession | None = None
        self._lock = asyncio.Lock()

    async def open(self, on_bytes, cols: int = 120, rows: int = 32) -> None:
        async with self._lock:
            if self.session:
                await self.session.close()
            session = ShellSession()
            await session.start(on_bytes, cols, rows)
            self.session = session

    def write(self, data: bytes) -> None:
        if self.session:
            self.session.write(data)

    def resize(self, cols: int, rows: int) -> None:
        if self.session:
            self.session.resize(cols, rows)

    async def close(self) -> None:
        async with self._lock:
            if self.session:
                await self.session.close()
                self.session = None


shells = ShellManager()
