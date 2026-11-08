"""Запуск FFmpeg/FFprobe с таймаутом без shell-инъекций."""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass

from .config import music_settings
from .errors import ProcessingError

log = logging.getLogger("music.ffmpeg")
_SEM = asyncio.Semaphore(2)


@dataclass(slots=True)
class ProcessResult:
    returncode: int
    stdout: bytes = b""
    stderr: str = ""


def ffmpeg_path() -> str:
    return shutil.which(music_settings.ffmpeg_bin) or music_settings.ffmpeg_bin


def ffprobe_path() -> str:
    return shutil.which(music_settings.ffprobe_bin) or music_settings.ffprobe_bin


async def run_process(cmd: list[str], *, timeout: float | None = None,
                      max_stdout: int = 64 * 1024 * 1024) -> ProcessResult:
    async with _SEM:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError as exc:
            raise ProcessingError(f"Не найден исполняемый файл: {cmd[0]}",
                                  code="FFMPEG_MISSING") from exc
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=timeout or music_settings.ffmpeg_timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise ProcessingError(f"Таймаут {cmd[0]}", code="FFMPEG_TIMEOUT", retryable=True)
        if len(out) > max_stdout:
            out = out[:max_stdout]
        return ProcessResult(proc.returncode or 0, out,
                             err.decode("utf-8", errors="replace"))


async def ffprobe_json(path: str, *, timeout: float | None = None) -> ProcessResult:
    return await run_process([
        ffprobe_path(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-i", str(path),
    ], timeout=timeout)


__all__ = ["ProcessResult", "run_process", "ffprobe_json", "ffmpeg_path", "ffprobe_path"]
