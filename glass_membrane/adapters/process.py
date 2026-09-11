"""Subprocess runner shared by the CLI adapters.

One short-lived process per call: prompt on stdin, stdout collected line by
line, a hard timeout, and a process-tree kill on timeout or cancellation so no
provider process outlives its assignment.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

STREAM_LIMIT = 32 * 1024 * 1024  # stream-json lines can be large


@dataclass
class ProcessResult:
    returncode: int | None
    stdout_lines: list[str] = field(default_factory=list)
    stderr: str = ""
    timed_out: bool = False


def resolve_command(command: str) -> list[str]:
    path = shutil.which(command)
    if path is None:
        raise FileNotFoundError(f"{command!r} was not found on PATH")
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", path]
    return [path]


async def kill_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(proc.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await killer.wait()
    else:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), 5)


async def run_process(argv: list[str], *, stdin_text: str, cwd: Path, env: dict[str, str] | None,
                      timeout: float, on_line: Callable[[str], None] | None = None) -> ProcessResult:
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=str(cwd), env=env, limit=STREAM_LIMIT, **extra)
    result = ProcessResult(returncode=None)

    async def feed() -> None:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            proc.stdin.write(stdin_text.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

    async def read_stdout() -> None:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            result.stdout_lines.append(text)
            if on_line:
                on_line(text)

    async def read_stderr() -> None:
        data = await proc.stderr.read()
        result.stderr = data.decode("utf-8", errors="replace")[-8000:]

    try:
        await asyncio.wait_for(asyncio.gather(feed(), read_stdout(), read_stderr(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        result.timed_out = True
        await kill_tree(proc)
    except asyncio.CancelledError:
        await kill_tree(proc)
        raise
    result.returncode = proc.returncode
    return result
