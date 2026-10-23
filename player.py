"""Playback engine for the MTS Music clients.

ffplay supports pause via SIGSTOP/SIGCONT. Seeking restarts the stream with
`-ss` at the requested offset, which keeps the implementation tiny and avoids
requiring mpv/libmpv.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import time
from pathlib import Path


class MusicPlayer:
    def __init__(self, explicit: str | None = None, volume: int = 80) -> None:
        self.path = self._pick(explicit)
        self.proc: asyncio.subprocess.Process | None = None
        self.url: str | None = None
        self.offset = 0.0
        self.paused = False
        self.started_at = 0.0
        self.paused_total = 0.0
        self.paused_at = 0.0
        self.switching = False
        self.volume = max(0, min(100, int(volume)))

    @staticmethod
    def _pick(explicit: str | None) -> str | None:
        if explicit:
            return explicit
        env = os.environ.get("MTSMUSIC_PLAYER")
        if env:
            return env
        for candidate in ("mpv", "ffplay"):
            found = shutil.which(candidate)
            if found:
                return found
        return None

    def _command(self, url: str, start: float = 0.0) -> list[str]:
        name = Path(self.path or "").name
        if name == "ffplay":
            command = [
                self.path or "ffplay",
                "-nodisp",
                "-autoexit",
                "-volume",
                str(self.volume),
                "-loglevel",
                "error",
            ]
            if start > 0:
                command += ["-ss", f"{start:.3f}"]
            return command + [url]
        command = [
            self.path or "mpv",
            "--no-video",
            "--really-quiet",
            f"--volume={self.volume}",
        ]
        if start > 0:
            command.append(f"--start={start:.3f}")
        return command + [url]

    async def set_volume(self, volume: int) -> None:
        """Меняет громкость; ffplay/mpv не умеют на лету, поэтому перезапускаем."""
        self.volume = max(0, min(100, int(volume)))
        if self.url and self.is_playing():
            position = self.elapsed()
            was_paused = self.paused
            await self.play(self.url, position)
            if was_paused:
                self.pause()

    async def play(self, url: str, start: float = 0.0) -> None:
        await self.stop()
        if not self.path:
            raise RuntimeError("не найден mpv/ffplay")
        self.switching = True
        self.url = url
        self.offset = max(0.0, start)
        self.proc = await asyncio.create_subprocess_exec(
            *self._command(url, self.offset),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.paused = False
        self.started_at = time.monotonic()
        self.paused_total = 0.0
        self.switching = False

    async def stop(self) -> None:
        self.switching = True
        proc = self.proc
        if proc and proc.returncode is None:
            if self.paused:
                try:
                    proc.send_signal(signal.SIGCONT)
                except ProcessLookupError:
                    pass
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        self.proc = None
        self.paused = False
        self.switching = False

    def pause(self) -> None:
        if self.proc and self.proc.returncode is None and not self.paused:
            try:
                self.proc.send_signal(signal.SIGSTOP)
            except ProcessLookupError:
                return
            self.paused = True
            self.paused_at = time.monotonic()

    def resume(self) -> None:
        if self.proc and self.proc.returncode is None and self.paused:
            try:
                self.proc.send_signal(signal.SIGCONT)
            except ProcessLookupError:
                return
            self.paused = False
            self.paused_total += time.monotonic() - self.paused_at

    def toggle_pause(self) -> bool:
        if self.paused:
            self.resume()
        else:
            self.pause()
        return self.paused

    def is_playing(self) -> bool:
        return bool(self.proc and self.proc.returncode is None)

    def elapsed(self) -> float:
        if not self.proc:
            return 0.0
        now = self.paused_at if self.paused else time.monotonic()
        return max(0.0, self.offset + now - self.started_at - self.paused_total)

    async def seek(self, delta: float, duration: float) -> None:
        """Seek by delta seconds. Restarts ffplay at the new position."""
        if not self.url or not self.is_playing():
            return
        limit = max(1.0, duration - 1.0) if duration else None
        target = max(0.0, self.elapsed() + delta)
        if limit:
            target = min(target, limit)
        was_paused = self.paused
        await self.play(self.url, target)
        if was_paused:
            self.pause()
