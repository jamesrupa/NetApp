"""Cross-platform helpers for running OS commands from async code."""

from __future__ import annotations

import asyncio
import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

OS = platform.system()  # "Windows", "Darwin", "Linux"
IS_WINDOWS = OS == "Windows"
IS_MAC = OS == "Darwin"
IS_LINUX = OS == "Linux"

# Subprocesses run in a dedicated thread pool instead of asyncio's subprocess API:
# it behaves the same under every event loop (uvicorn on Windows included) and lets
# a network sweep run many pings in parallel.
_executor = ThreadPoolExecutor(max_workers=64, thread_name_prefix="netapp-cmd")


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_cmd_sync(args: list[str], timeout: float = 10.0) -> CmdResult | None:
    """Run a command and capture its output. Returns None if it is not installed or times out."""
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return None
    return CmdResult(proc.returncode, proc.stdout or "", proc.stderr or "")


async def run_cmd(args: list[str], timeout: float = 10.0) -> CmdResult | None:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, run_cmd_sync, args, timeout)


async def run_blocking(func, *args):
    """Run any blocking callable (DNS lookups, etc.) in the shared pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, func, *args)
