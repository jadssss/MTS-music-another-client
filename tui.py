#!/usr/bin/env python3
"""Полноценный TUI-плеер MTS Music на Textual.

Запуск:  python mtsmusic.py tui
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import signal
import time
from pathlib import Path

import requests
from PIL import Image
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    ProgressBar,
    Static,
    TabbedContent,
    TabPane,
)

from mtsmusic import (
    USER_AGENT,
    Api,
    album_id,
    fetch_mix_batch,
    fmt_duration,
    force_ipv4,
    get_uid,
    resolve_stream,
)

COVER_WIDTH = 38
COVER_SIZE = 360
WAVE_BATCH = 5


# ---------------------------------------------------------------------------
# данные треков
# ---------------------------------------------------------------------------


def track_artist(track: dict) -> str:
    return ", ".join(a.get("name", "?") for a in track.get("artists") or []) or "?"


def track_album(track: dict) -> str:
    albums = track.get("albums") or []
    return (albums[0].get("title") or "—") if albums else "—"


def track_cover_url(track: dict) -> str | None:
    uri = track.get("coverUri")
    if not uri:
        albums = track.get("albums") or []
        uri = albums[0].get("coverUri") if albums else None
    if not uri:
        return None
    uri = uri.replace("%%", f"{COVER_SIZE}x{COVER_SIZE}")
    if not uri.startswith("http"):
        uri = "https://" + uri
    return uri


def cover_renderable(data: bytes, width: int = COVER_WIDTH) -> Text:
    """Картинка в терминале: две вертикальные точки на один символ ▀."""
    image = Image.open(io.BytesIO(data)).convert("RGB")
    rows = max(1, width // 2)
    image = image.resize((width, rows * 2), Image.Resampling.LANCZOS)

    canvas = Text()
    for y in range(rows):
        for x in range(width):
            top = image.getpixel((x, y * 2))
            bottom = image.getpixel((x, y * 2 + 1))
            canvas.append(
                "▀",
                style=f"#{top[0]:02x}{top[1]:02x}{top[2]:02x} "
                f"on #{bottom[0]:02x}{bottom[1]:02x}{bottom[2]:02x}",
            )
        if y != rows - 1:
            canvas.append("\n")
    return canvas


# ---------------------------------------------------------------------------
# плеер
# ---------------------------------------------------------------------------


class MusicPlayer:
    """ffplay/mpv + пауза через SIGSTOP/SIGCONT."""

    def __init__(self, explicit: str | None = None) -> None:
        self.path = self._pick(explicit)
        self.proc: asyncio.subprocess.Process | None = None
        self.paused = False
        self.started_at = 0.0
        self.paused_total = 0.0
        self.paused_at = 0.0
        self.switching = False

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

    def _command(self, url: str) -> list[str]:
        name = Path(self.path or "").name
        if name == "ffplay":
            return [self.path or "ffplay", "-nodisp", "-autoexit", "-loglevel", "error", url]
        return [self.path or "mpv", "--no-video", "--really-quiet", url]

    async def play(self, url: str) -> None:
        await self.stop()
        if not self.path:
            raise RuntimeError("не найден mpv/ffplay")
        self.switching = True
        self.proc = await asyncio.create_subprocess_exec(
            *self._command(url),
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
        return max(0.0, now - self.started_at - self.paused_total)


# ---------------------------------------------------------------------------
# приложение
# ---------------------------------------------------------------------------


class MusicTui(App):
    TITLE = "MTS Music"
    SUB_TITLE = "Моя волна"

    CSS = """
    Screen { layout: vertical; }

    #main { height: 1fr; }

    #left {
        width: 44;
        min-width: 44;
        padding: 1 1 0 1;
        border-right: solid $primary-darken-2;
    }

    #cover {
        width: 40;
        height: 22;
        content-align: center middle;
        color: $text-muted;
    }

    #info {
        height: auto;
        padding: 1 0 0 0;
    }

    #progress {
        margin: 1 0 0 0;
    }

    #time {
        height: 1;
        color: $text-muted;
        text-align: right;
    }

    #right { width: 1fr; padding: 0 1; }

    TabbedContent { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    DataTable { height: 1fr; }

    #controls {
        height: 3;
        align: center middle;
        padding: 0 1;
    }

    #controls Button {
        margin: 0 1;
        min-width: 10;
    }

    #like { min-width: 14; }
    #status { width: 1fr; content-align: right middle; color: $text-muted; }
    """

    BINDINGS = [
        Binding("space", "toggle_pause", "Пауза", show=True),
        Binding("n", "next_track", "Следующий", show=True),
        Binding("p", "prev_track", "Предыдущий", show=True),
        Binding("l", "toggle_like", "В избранное", show=True),
        Binding("f", "show_favorites", "Избранное", show=True),
        Binding("r", "refresh_wave", "Обновить волну", show=False),
        Binding("q", "quit", "Выход", show=True),
    ]

    def __init__(self, player: str | None = None) -> None:
        super().__init__()
        self.explicit_player = player
        self.api: Api | None = None
        self.uid = ""
        self.player = MusicPlayer(player)
        self.wave: list[dict] = []
        self.favorites: list[dict] = []
        self.liked_ids: set[str] = set()
        self.queue: list[dict] = []
        self.queue_name = "wave"
        self.current: dict | None = None
        self.current_index = -1
        self.play_token = 0

    # -- разметка ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("Загрузка…", id="cover")
                yield Static("", id="info")
                yield ProgressBar(total=100, show_eta=False, id="progress")
                yield Static("0:00 / 0:00", id="time")
            with Vertical(id="right"):
                with TabbedContent(id="tabs"):
                    with TabPane("Моя волна", id="tab-wave"):
                        yield DataTable(id="wave-table", cursor_type="row", zebra_stripes=True)
                    with TabPane("Избранное", id="tab-fav"):
                        yield DataTable(id="fav-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="controls"):
            yield Button("⏮", id="prev", variant="default")
            yield Button("▶", id="playpause", variant="primary")
            yield Button("⏭", id="next", variant="default")
            yield Button("♡ В избранное", id="like", variant="success")
            yield Button("⟳ Волна", id="refresh", variant="default")
            yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        wave_keys = self.query_one("#wave-table", DataTable).add_columns(
            "#", "Трек", "Исполнитель", "Альбом", "Длит.", "♥"
        )
        fav_keys = self.query_one("#fav-table", DataTable).add_columns(
            "#", "Трек", "Исполнитель", "Альбом", "Длит.", "♥"
        )
        self.like_columns = {
            "wave-table": wave_keys[-1],
            "fav-table": fav_keys[-1],
        }
        self.set_interval(0.4, self.tick)

        try:
            self.api = Api()
        except SystemExit:
            self.exit(message="Не удалось инициализировать API")
            return
        if not self.api.token:
            self.notify("Сначала войди: python mtsmusic.py login", severity="error", timeout=10)
            self.set_timer(3, self.exit)
            return
        self.load_initial()

    async def on_unmount(self) -> None:
        await self.player.stop()

    # -- загрузка ---------------------------------------------------------

    @work(thread=True, exclusive=True)
    def load_initial(self) -> None:
        assert self.api
        try:
            self.uid = get_uid(self.api)
            batch = fetch_mix_batch(self.api)
            wave = [
                item["track"]
                for item in (batch.get("sequence") or [])
                if item.get("type") == "track" and item.get("track")
            ]
            likes = self.api.get(f"/users/{self.uid}/likes/tracks")
            refs = ((likes.get("result") or {}).get("library") or {}).get("tracks") or []
            ids = [str(ref.get("id")) for ref in refs]
            favorites: list[dict] = []
            for start in range(0, len(ids), 50):
                chunk = ids[start : start + 50]
                if not chunk:
                    continue
                favorites.extend(
                    self.api.get("/tracks", trackIds=",".join(chunk)).get("result") or []
                )
            self.call_from_thread(self.apply_initial, wave, favorites)
        except SystemExit as exc:
            self.call_from_thread(self.notify, f"Ошибка загрузки: {exc}", severity="error")
        except requests.RequestException as exc:
            self.call_from_thread(self.notify, f"Сеть недоступна: {exc}", severity="error")

    def apply_initial(self, wave: list[dict], favorites: list[dict]) -> None:
        self.wave = wave
        self.favorites = favorites
        self.liked_ids = {str(track.get("id")) for track in favorites}
        self.fill_table(self.query_one("#wave-table", DataTable), self.wave)
        self.fill_table(self.query_one("#fav-table", DataTable), self.favorites)
        self.set_status(f"Волна: {len(self.wave)} · избранное: {len(self.favorites)}")
        if self.wave:
            self.select_first("#wave-table")
            self.current = self.wave[0]
            self.current_index = 0
            self.queue = self.wave
            self.queue_name = "wave"
            self.update_info()
            self.load_cover(self.wave[0])

    def fill_table(self, table: DataTable, tracks: list[dict]) -> None:
        table.clear()
        for index, track in enumerate(tracks, 1):
            heart = "♥" if str(track.get("id")) in self.liked_ids else ""
            table.add_row(
                str(index),
                track.get("title") or "?",
                track_artist(track),
                track_album(track),
                fmt_duration(track.get("durationMs")),
                heart,
                key=str(track.get("id")),
            )

    def add_wave_tracks(self, tracks: list[dict]) -> None:
        table = self.query_one("#wave-table", DataTable)
        known = {str(track.get("id")) for track in self.wave}
        added = 0
        for track in tracks:
            track_id = str(track.get("id"))
            if not track_id or track_id in known:
                continue
            known.add(track_id)
            self.wave.append(track)
            heart = "♥" if track_id in self.liked_ids else ""
            table.add_row(
                str(len(self.wave)),
                track.get("title") or "?",
                track_artist(track),
                track_album(track),
                fmt_duration(track.get("durationMs")),
                heart,
                key=track_id,
            )
            added += 1
        if added:
            self.set_status(f"Волна: {len(self.wave)} · избранное: {len(self.favorites)}")

    def select_first(self, table_id: str) -> None:
        table = self.query_one(table_id, DataTable)
        if table.row_count:
            table.move_cursor(row=0)

    # -- информация о треке ----------------------------------------------

    def update_info(self) -> None:
        track = self.current
        info = self.query_one("#info", Static)
        if not track:
            info.update(Text("Ничего не играет", style="dim"))
            return

        track_id = str(track.get("id"))
        liked = track_id in self.liked_ids
        lines = Text()
        lines.append(track.get("title") or "?", style="bold")
        lines.append("\n")
        lines.append(track_artist(track), style="italic")
        lines.append("\n")
        lines.append(track_album(track), style="dim")
        lines.append("\n")
        lines.append(f"{fmt_duration(track.get('durationMs'))}  ")
        lines.append("♥ в избранном" if liked else "♡ не в избранном", style="red" if liked else "dim")

        info.update(lines)
        button = self.query_one("#like", Button)
        button.label = "♥ Убрать" if liked else "♡ В избранное"
        button.variant = "warning" if liked else "success"
        self.update_play_button()

    def update_play_button(self) -> None:
        button = self.query_one("#playpause", Button)
        button.label = "⏸" if self.player.paused else "▶"

    def update_time(self) -> None:
        duration = (self.current or {}).get("durationMs") or 0
        elapsed = min(self.player.elapsed(), duration / 1000 if duration else 0)
        bar = self.query_one("#progress", ProgressBar)
        bar.update(total=max(duration / 1000, 1), progress=elapsed)
        label = self.query_one("#time", Static)
        label.update(f"{fmt_duration(int(elapsed * 1000))} / {fmt_duration(duration)}")

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    # -- обложка ----------------------------------------------------------

    @work(thread=True, exclusive=True, group="cover")
    def load_cover(self, track: dict) -> None:
        url = track_cover_url(track)
        if not url:
            self.call_from_thread(self.show_placeholder_cover)
            return
        try:
            force_ipv4()
            response = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=20
            )
            response.raise_for_status()
            renderable = cover_renderable(response.content)
            self.call_from_thread(self.show_cover, renderable)
        except (requests.RequestException, OSError, ValueError):
            self.call_from_thread(self.show_placeholder_cover)

    def show_cover(self, renderable: Text) -> None:
        self.query_one("#cover", Static).update(renderable)

    def show_placeholder_cover(self) -> None:
        self.query_one("#cover", Static).update("♪\n\nнет обложки")

    # -- воспроизведение --------------------------------------------------

    def play_track(self, track: dict, queue: list[dict] | None = None, index: int | None = None) -> None:
        if queue is not None:
            self.queue = queue
        if index is not None:
            self.current_index = index
        else:
            self.current_index = next(
                (i for i, item in enumerate(self.queue) if str(item.get("id")) == str(track.get("id"))),
                -1,
            )
        self.current = track
        self.play_token += 1
        token = self.play_token
        self.update_info()
        self.update_time()
        self.load_cover(track)
        self.set_status("Загружаю поток…")
        self.fetch_stream(token, track)

    @work(thread=True, exclusive=False, group="stream")
    def fetch_stream(self, token: int, track: dict) -> None:
        assert self.api
        try:
            url, item = resolve_stream(self.api, str(track.get("id")))
        except SystemExit as exc:
            self.call_from_thread(
                self.notify, f"Не удалось открыть трек: {exc}", severity="error"
            )
            return
        self.call_from_thread(
            self.start_playback, token, track, url, item.get("preview", False)
        )

    def start_playback(self, token: int, track: dict, url: str, preview: bool) -> None:
        if token != self.play_token:
            return
        self.run_worker(self._play_url(track, url, preview), group="playback")

    async def _play_url(self, track: dict, url: str, preview: bool) -> None:
        try:
            await self.player.play(url)
        except (RuntimeError, OSError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.update_play_button()
        self.set_status(
            ("preview · " if preview else "")
            + f"Играет: {track_artist(track)} — {track.get('title')}"
        )

    def action_toggle_pause(self) -> None:
        if not self.player.is_playing():
            self.action_play_current()
            return
        self.player.toggle_pause()
        self.update_play_button()

    def action_play_current(self) -> None:
        if self.current:
            queue = self.queue or self.wave
            index = self.current_index if 0 <= self.current_index < len(queue) else None
            self.play_track(self.current, queue, index)

    def try_next(self, direction: int) -> None:
        queue = self.queue or self.wave
        if not queue:
            return
        index = self.current_index + direction
        if index >= len(queue):
            if self.queue_name == "wave":
                self.load_more_wave(then_play=index)
                return
            index = 0
        if index < 0:
            index = len(queue) - 1
        self.current_index = index
        self.play_track(queue[index], queue, index)

    def action_next_track(self) -> None:
        self.try_next(1)

    def action_prev_track(self) -> None:
        self.try_next(-1)

    def action_show_favorites(self) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.active = "tab-fav"
        table = self.query_one("#fav-table", DataTable)
        if table.row_count:
            table.focus()

    def action_refresh_wave(self) -> None:
        self.load_more_wave(refresh=True)

    @work(thread=True, exclusive=True, group="wave")
    def load_more_wave(self, then_play: int | None = None, refresh: bool = False) -> None:
        assert self.api
        try:
            if refresh:
                queue = []
            else:
                ids = [str(track.get("id")) for track in self.wave[-WAVE_BATCH:]]
                queue = ids if ids else []
            batch = fetch_mix_batch(self.api, queue or None)
            tracks = [
                item["track"]
                for item in (batch.get("sequence") or [])
                if item.get("type") == "track" and item.get("track")
            ]
            self.call_from_thread(self.apply_more_wave, tracks, then_play)
        except SystemExit as exc:
            self.call_from_thread(self.notify, f"Ошибка волны: {exc}", severity="error")

    def apply_more_wave(self, tracks: list[dict], then_play: int | None) -> None:
        if not tracks:
            self.notify("Волна не вернула новых треков", severity="warning")
            return
        self.add_wave_tracks(tracks)
        if then_play is not None and then_play < len(self.queue):
            self.play_track(self.queue[then_play], self.queue, then_play)

    # -- избранное --------------------------------------------------------

    def action_toggle_like(self) -> None:
        if not self.current:
            return
        self.toggle_like(self.current)

    @work(thread=True, exclusive=True, group="like")
    def toggle_like(self, track: dict) -> None:
        assert self.api
        track_id = str(track.get("id"))
        album = album_id(track)
        if not album:
            self.call_from_thread(self.notify, "У трека нет альбома", severity="error")
            return
        liked = track_id in self.liked_ids
        try:
            if liked:
                self.api.post(
                    f"/users/{self.uid}/likes/tracks/remove",
                    **{"track-ids": f"{track_id}:{album}"},
                )
            else:
                self.api.post(
                    f"/users/{self.uid}/likes/tracks/add",
                    **{"track-id": f"{track_id}:{album}"},
                )
        except SystemExit as exc:
            self.call_from_thread(self.notify, f"Ошибка избранного: {exc}", severity="error")
            return
        self.call_from_thread(self.apply_like, track, not liked)

    def apply_like(self, track: dict, liked: bool) -> None:
        track_id = str(track.get("id"))
        if liked:
            self.liked_ids.add(track_id)
            if not any(str(item.get("id")) == track_id for item in self.favorites):
                self.favorites.insert(0, track)
        else:
            self.liked_ids.discard(track_id)
            self.favorites = [
                item for item in self.favorites if str(item.get("id")) != track_id
            ]

        wave_table = self.query_one("#wave-table", DataTable)
        if track_id in {str(item.get("id")) for item in self.wave}:
            try:
                wave_table.update_cell(
                    track_id,
                    self.like_columns["wave-table"],
                    "♥" if liked else "",
                )
            except Exception:
                pass
        self.fill_table(self.query_one("#fav-table", DataTable), self.favorites)
        self.update_info()
        self.notify(
            "♥ Добавлено в избранное" if liked else "♡ Убрано из избранного",
            timeout=2,
        )
        self.set_status(f"Волна: {len(self.wave)} · избранное: {len(self.favorites)}")

    # -- события ----------------------------------------------------------

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        table = event.data_table
        track_id = str(event.row_key.value)
        if table.id == "wave-table":
            queue, name = self.wave, "wave"
        else:
            queue, name = self.favorites, "fav"
        index = next(
            (i for i, track in enumerate(queue) if str(track.get("id")) == track_id),
            -1,
        )
        if index < 0:
            return
        self.queue_name = name
        self.play_track(queue[index], queue, index)

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "prev": self.action_prev_track,
            "playpause": self.action_toggle_pause,
            "next": self.action_next_track,
            "like": self.action_toggle_like,
            "refresh": self.action_refresh_wave,
        }
        handler = handlers.get(event.button.id or "")
        if handler:
            handler()

    # -- таймер -----------------------------------------------------------

    def tick(self) -> None:
        proc = self.player.proc
        if proc is None:
            if self.current:
                self.update_time()
            return
        if self.player.switching:
            return
        if proc.returncode is not None:
            # Трек закончился — сразу переходим к следующему.
            self.player.proc = None
            self.action_next_track()
            return
        self.update_time()
