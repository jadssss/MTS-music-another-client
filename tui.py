#!/usr/bin/env python3
"""Mobile-style TUI player for MTS Music (Textual)."""

from __future__ import annotations

import io

import requests
from PIL import Image
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click, Resize
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    ProgressBar,
    Static,
)

from media import (
    load_history,
    record_history,
    track_album,
    track_artist,
    track_cover_url,
)
from mtsmusic import (
    USER_AGENT,
    Api,
    fmt_duration,
    force_ipv4,
    get_uid,
    lrclib_lyrics,
    lyrics_response,
    lyrics_text,
    resolve_stream,
)
from player import MusicPlayer

COVER_MAX = 46
COVER_MIN = 20
COVER_CACHE = 20

MONO_THEME = Theme(
    name="mono",
    primary="#e6e6e6",
    secondary="#9a9a9a",
    warning="#cfcfcf",
    error="#ffffff",
    success="#bdbdbd",
    accent="#ffffff",
    foreground="#f2f2f2",
    background="#000000",
    surface="#0d0d0d",
    panel="#0a0a0a",
    dark=True,
)


def cover_renderable(data: bytes, width: int) -> Text:
    """Обложка в оттенках серого (полностью чёрно-белый интерфейс)."""
    image = Image.open(io.BytesIO(data)).convert("L")
    rows = max(1, width // 2)
    image = image.resize((width, rows * 2), Image.Resampling.LANCZOS)

    canvas = Text()
    for y in range(rows):
        for x in range(width):
            top = image.getpixel((x, y * 2))
            bottom = image.getpixel((x, y * 2 + 1))
            canvas.append(
                "▀",
                style=f"#{top:02x}{top:02x}{top:02x} "
                f"on #{bottom:02x}{bottom:02x}{bottom:02x}",
            )
        if y != rows - 1:
            canvas.append("\n")
    return canvas


def make_topbar(title: str, back: bool = True) -> Horizontal:
    widgets = []
    if back:
        widgets.append(Button("←", id="back"))
    widgets.append(Static(title, id="screen-title"))
    return Horizontal(*widgets, id="screen-top")


def track_row(track: dict) -> tuple[str, str, str, str]:
    return (
        track.get("title") or "?",
        track_artist(track),
        track_album(track),
        fmt_duration(track.get("durationMs")),
    )


# ---------------------------------------------------------------------------
# боковые экраны
# ---------------------------------------------------------------------------


class ListScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Назад")]

    def __init__(self, title: str, tracks: list[dict]) -> None:
        super().__init__()
        self.list_title = title
        self.tracks = list(tracks)

    def compose(self) -> ComposeResult:
        yield make_topbar(self.list_title)
        yield DataTable(id="list-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#list-table", DataTable)
        table.add_columns("Трек", "Исполнитель", "Альбом", "Длит.")
        self.fill(self.tracks)

    def fill(self, tracks: list[dict]) -> None:
        self.tracks = tracks
        table = self.query_one("#list-table", DataTable)
        table.clear()
        for track in tracks:
            table.add_row(*track_row(track), key=str(track.get("id")))

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back")
    def back_pressed(self) -> None:
        self.action_back()

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        track_id = str(event.row_key.value)
        index = next(
            (i for i, item in enumerate(self.tracks) if str(item.get("id")) == track_id),
            -1,
        )
        if index >= 0:
            self.app.play_from_list(self.tracks, index, self.list_title)
            self.app.pop_screen()


class FavoritesScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        yield make_topbar("Избранное")
        yield Static("Загружаю…", id="screen-status")
        yield DataTable(id="list-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#list-table", DataTable)
        table.add_columns("Трек", "Исполнитель", "Альбом", "Длит.")
        self.load()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back")
    def back_pressed(self) -> None:
        self.action_back()

    @work(thread=True, exclusive=True, group="favorites")
    def load(self) -> None:
        api = self.app.api
        if not api:
            return
        try:
            uid = self.app.uid or get_uid(api)
            payload = api.get(f"/users/{uid}/likes/tracks")
            refs = ((payload.get("result") or {}).get("library") or {}).get("tracks") or []
            ids = [str(ref.get("id")) for ref in refs[:300]]
            tracks: list[dict] = []
            for start in range(0, len(ids), 50):
                chunk = ids[start : start + 50]
                if not chunk:
                    continue
                tracks.extend(
                    api.get("/tracks", trackIds=",".join(chunk)).get("result") or []
                )
            self.app.call_from_thread(self.fill, tracks)
        except SystemExit as exc:
            self.app.call_from_thread(self.failed, str(exc))

    def fill(self, tracks: list[dict]) -> None:
        table = self.query_one("#list-table", DataTable)
        table.clear()
        for track in tracks:
            table.add_row(*track_row(track), key=str(track.get("id")))
        self.app.favorites = tracks
        self.query_one("#screen-status", Static).update(
            f"{len(tracks)} трек(ов)" if tracks else "Пока пусто"
        )

    def failed(self, message: str) -> None:
        self.query_one("#screen-status", Static).update(f"Ошибка: {message}")

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        track_id = str(event.row_key.value)
        tracks = self.app.favorites
        index = next(
            (i for i, item in enumerate(tracks) if str(item.get("id")) == track_id),
            -1,
        )
        if index >= 0:
            self.app.play_from_list(tracks, index, "Избранное")
            self.app.pop_screen()


class HistoryScreen(ListScreen):
    def __init__(self, tracks: list[dict]) -> None:
        super().__init__("История", tracks)


class SearchScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Назад")]

    def compose(self) -> ComposeResult:
        yield make_topbar("Поиск")
        with Horizontal(id="search-row"):
            yield Input(placeholder="Исполнитель или трек…", id="search-input")
            yield Button("Найти", id="search-go", variant="primary")
        yield Static("", id="screen-status")
        yield DataTable(id="list-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#list-table", DataTable)
        table.add_columns("Трек", "Исполнитель", "Альбом", "Длит.")
        self.results: list[dict] = []
        self.query_one("#search-input", Input).focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back")
    def back_pressed(self) -> None:
        self.action_back()

    @on(Input.Submitted)
    def submitted(self) -> None:
        self.search()

    @on(Button.Pressed, "#search-go")
    def search_pressed(self) -> None:
        self.search()

    def search(self) -> None:
        query = self.query_one("#search-input", Input).value.strip()
        if not query:
            return
        self.query_one("#screen-status", Static).update("Ищу…")
        self.do_search(query)

    @work(thread=True, exclusive=True, group="search")
    def do_search(self, query: str) -> None:
        api = self.app.api
        if not api:
            return
        try:
            payload = api.get(
                "/search",
                text=query,
                type="track",
                page=0,
                **{"page-size": 25},
                nocorrect="true",
            )
            tracks = (((payload.get("result") or {}).get("tracks") or {}).get("results")) or []
            self.app.call_from_thread(self.fill, tracks)
        except SystemExit as exc:
            self.app.call_from_thread(self.failed, str(exc))

    def fill(self, tracks: list[dict]) -> None:
        self.results = tracks
        table = self.query_one("#list-table", DataTable)
        table.clear()
        for track in tracks:
            table.add_row(*track_row(track), key=str(track.get("id")))
        self.query_one("#screen-status", Static).update(
            f"{len(tracks)} результат(ов)" if tracks else "Ничего не найдено"
        )

    def failed(self, message: str) -> None:
        self.query_one("#screen-status", Static).update(f"Ошибка: {message}")

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        track_id = str(event.row_key.value)
        index = next(
            (i for i, item in enumerate(self.results) if str(item.get("id")) == track_id),
            -1,
        )
        if index >= 0:
            self.app.play_from_list(self.results, index, "Поиск")
            self.app.pop_screen()


class LyricsScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Назад")]

    def __init__(self, track: dict) -> None:
        super().__init__()
        self.track = track

    def compose(self) -> ComposeResult:
        title = self.track.get("title") or "Текст"
        yield make_topbar(f"Текст · {title}")
        yield Static("Загружаю текст…", id="screen-status")
        with VerticalScroll(id="lyrics-scroll"):
            yield Static("", id="lyrics-body")

    def on_mount(self) -> None:
        self.load()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back")
    def back_pressed(self) -> None:
        self.action_back()

    @work(thread=True, exclusive=True, group="lyrics")
    def load(self) -> None:
        api = self.app.api
        if not api:
            return
        text = ""
        source = "MTS"
        try:
            response = lyrics_response(api, str(self.track.get("id")), "TEXT")
            if response.status_code == 200:
                text = lyrics_text(api, response)
                if "Подключите подписку" in text:
                    text = ""
        except Exception:
            text = ""
        if not text.strip():
            text = lrclib_lyrics(self.track, want_sync=False) or ""
            source = "LRCLIB"
        self.app.call_from_thread(self.fill, text, source)

    def fill(self, text: str, source: str) -> None:
        status = self.query_one("#screen-status", Static)
        body = self.query_one("#lyrics-body", Static)
        if not text.strip():
            status.update("Текст не найден")
            body.update("")
            return
        status.update(f"Источник: {source}")
        body.update(Text(text))


class MenuScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "close", "Закрыть")]

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-panel"):
            yield Static("Меню", id="menu-title")
            yield Button("Моя волна", id="m-wave")
            yield Button("Избранное", id="m-favorites")
            yield Button("История", id="m-history")
            yield Button("Поиск", id="m-search")
            yield Button("Текст песни", id="m-lyrics")
            yield Static("", id="menu-spacer")
            yield Button("Выход", id="m-quit", variant="error")

    def action_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)


# ---------------------------------------------------------------------------
# главный экран
# ---------------------------------------------------------------------------


class MusicTui(App):
    TITLE = "MTS Music"

    CSS = """
    /* Полностью чёрно-белая тема: только оттенки серого. */
    Screen { background: #000000; color: #f2f2f2; }

    Button {
        background: #101010;
        color: #f2f2f2;
        border: tall #4a4a4a;
        text-style: bold;
    }
    Button:hover { background: #1c1c1c; border: tall #9a9a9a; }
    Button:focus { border: tall #ffffff; }
    Button.-active { background: #2a2a2a; }

    #topbar {
        height: 3;
        dock: top;
        padding: 0 1;
        align: left middle;
        background: #0a0a0a;
    }
    #menu { width: 5; min-width: 5; margin: 0 1 0 0; }
    #brand { text-style: bold; padding: 0 1; color: #f2f2f2; }
    #topstatus { width: 1fr; text-align: right; content-align: right middle; color: #8a8a8a; }

    #phone {
        height: 1fr;
        align: center middle;
        padding: 1 0 0 0;
    }
    /* Колонка ровно по ширине обложки: всё под обложкой выровнено по ней. */
    #column { width: 40; height: auto; align: center top; }
    #cover {
        width: 100%;
        height: 20;
        content-align: center middle;
        color: #8a8a8a;
        margin-bottom: 1;
    }
    #title { width: 100%; text-align: center; text-style: bold; color: #ffffff; }
    #artist { width: 100%; text-align: center; color: #b0b0b0; }
    #album { width: 100%; text-align: center; color: #6a6a6a; }
    #time { width: 100%; text-align: center; color: #8a8a8a; margin-top: 1; }
    #progress { width: 100%; }
    #controls { height: 3; align: center middle; margin-top: 1; }
    #prev, #playpause, #next { height: 3; min-width: 4; }
    #actions { height: 3; align: center middle; margin-top: 1; }
    #actions Button { height: 3; min-width: 4; }

    #screen-top {
        height: 3;
        dock: top;
        padding: 0 1;
        align: left middle;
        background: #0a0a0a;
    }
    #back { width: 5; min-width: 5; margin-right: 1; }
    #screen-title { text-style: bold; width: 1fr; content-align: left middle; color: #f2f2f2; }
    #screen-status { height: 1; padding: 0 1; color: #8a8a8a; }
    #list-table { height: 1fr; background: #000000; }
    #search-row { height: 3; padding: 0 1; }
    #search-input { width: 1fr; }
    #search-go { width: 12; margin-left: 1; }
    #lyrics-scroll { height: 1fr; padding: 0 2; }
    #lyrics-body { width: 100%; color: #e0e0e0; }

    MenuScreen { align: left middle; background: #000000 70%; }
    #menu-panel {
        width: 42;
        height: 100%;
        background: #0d0d0d;
        border-right: thick #ffffff;
        padding: 1 2;
    }
    #menu-title { text-style: bold; padding: 0 0 1 0; color: #ffffff; }
    #menu-panel Button { width: 100%; margin: 1 0; }
    #menu-spacer { height: 1fr; }
    """

    BINDINGS = [
        Binding("space", "toggle_pause", "Пауза", show=True),
        Binding("left", "seek_back", "−5с", show=True),
        Binding("right", "seek_forward", "+5с", show=True),
        Binding("t", "show_lyrics", "Текст", show=True),
        Binding("l", "toggle_like", "Избранное", show=True),
        Binding("m", "menu", "Меню", show=True),
        Binding("q", "quit", "Выход", show=True),
        Binding("n", "next_track", "Следующий", show=False),
        Binding("p", "prev_track", "Предыдущий", show=False),
        Binding("f", "show_favorites", "Избранное", show=False),
        Binding("w", "show_wave", "Моя волна", show=False),
        Binding("h", "show_history", "История", show=False),
        Binding("s", "show_search", "Поиск", show=False),
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
        self.history: list[dict] = load_history()
        self.queue: list[dict] = []
        self.queue_name = "Моя волна"
        self.current: dict | None = None
        self.current_index = -1
        self.play_token = 0
        self.cover_token = 0
        self.cover_bytes: dict[str, bytes] = {}
        self.cover_width = 40

    # -- разметка ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Button("☰", id="menu")
            yield Static("MTS Music", id="brand")
            yield Static("", id="topstatus")
        with Horizontal(id="phone"):
            with Vertical(id="column"):
                yield Static("♪", id="cover")
                yield Static("", id="title")
                yield Static("", id="artist")
                yield Static("", id="album")
                yield Static("0:00 / 0:00", id="time")
                yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="progress")
                with Horizontal(id="controls"):
                    yield Button("⏮", id="prev")
                    yield Button("▶", id="playpause")
                    yield Button("⏭", id="next")
                with Horizontal(id="actions"):
                    yield Button("Текст", id="lyrics")
                    yield Button("♡ В избранное", id="like")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(MONO_THEME)
        self.theme = "mono"
        self.set_interval(0.4, self.tick)
        self.apply_cover_width()
        try:
            self.api = Api()
        except SystemExit:
            self.exit()
            return
        if not self.api.token:
            self.notify("Сначала войди: python mtsmusic.py login", severity="error", timeout=8)
            self.set_timer(3, self.exit)
            return
        self.load_initial()

    async def on_unmount(self) -> None:
        await self.player.stop()

    # -- геометрия --------------------------------------------------------

    def cover_width_for(self, width: int, height: int) -> int:
        budget = max(8, height - 19)
        value = min(COVER_MAX, budget * 2, max(COVER_MIN, width - 12))
        value = max(COVER_MIN, value)
        return value - (value % 2)

    def apply_cover_width(self) -> None:
        self.cover_width = self.cover_width_for(self.size.width, self.size.height)
        self.apply_column_width(self.cover_width)

    def apply_column_width(self, width: int) -> None:
        """Ширина колонки равна обложке; кнопки растянуты ровно по ней."""
        try:
            self.query_one("#column").styles.width = width
            self.query_one("#cover").styles.height = max(4, width // 2)
            side = max(4, width // 4)
            play = max(6, width - side * 2)
            self.query_one("#prev").styles.width = side
            self.query_one("#next").styles.width = side
            self.query_one("#playpause").styles.width = play
            half = max(5, width // 2)
            self.query_one("#lyrics").styles.width = half
            self.query_one("#like").styles.width = width - half
            self.update_like_button()
        except Exception:
            return

    def on_resize(self, event: Resize) -> None:
        width = self.cover_width_for(event.size.width, event.size.height)
        if width == self.cover_width:
            return
        self.cover_width = width
        self.apply_column_width(width)
        url = track_cover_url(self.current) if self.current else None
        if url and url in self.cover_bytes:
            self.show_cover(url)

    # -- загрузка ---------------------------------------------------------

    @work(thread=True, exclusive=True)
    def load_initial(self) -> None:
        api = self.api
        if not api:
            return
        try:
            self.uid = get_uid(api)
            from mtsmusic import fetch_mix_batch

            batch = fetch_mix_batch(api)
            wave = [
                item["track"]
                for item in (batch.get("sequence") or [])
                if item.get("type") == "track" and item.get("track")
            ]
            payload = api.get(f"/users/{self.uid}/likes/tracks")
            refs = ((payload.get("result") or {}).get("library") or {}).get("tracks") or []
            liked = {str(ref.get("id")) for ref in refs}
            self.call_from_thread(self.apply_initial, wave, liked)
        except SystemExit as exc:
            self.call_from_thread(self.notify, f"Ошибка загрузки: {exc}", severity="error")
        except requests.RequestException as exc:
            self.call_from_thread(self.notify, f"Сеть недоступна: {exc}", severity="error")

    def apply_initial(self, wave: list[dict], liked: set[str]) -> None:
        self.wave = wave
        self.liked_ids = liked
        self.set_topstatus(f"Волна · {len(self.wave)} · ♥ {len(self.liked_ids)}")
        if self.wave:
            self.current = self.wave[0]
            self.current_index = 0
            self.queue = self.wave
            self.queue_name = "Моя волна"
            self.update_now_playing()
            self.load_cover(self.wave[0])

    def set_topstatus(self, text: str) -> None:
        self.query_one("#topstatus", Static).update(text)

    # -- now playing ------------------------------------------------------

    def update_now_playing(self) -> None:
        track = self.current
        if not track:
            return
        track_id = str(track.get("id"))
        liked = track_id in self.liked_ids

        self.query_one("#title", Static).update(Text(track.get("title") or "?"))
        self.query_one("#artist", Static).update(Text(track_artist(track)))
        self.query_one("#album", Static).update(Text(track_album(track)))
        self.update_like_button(liked)
        self.update_play_button()
        self.update_time()
        self.sub_title = track.get("title") or ""

    def update_like_button(self, liked: bool | None = None) -> None:
        if liked is None:
            liked = bool(self.current and str(self.current.get("id")) in self.liked_ids)
        button = self.query_one("#like", Button)
        heart = "♥" if liked else "♡"
        suffix = " Избранное" if self.cover_width >= 34 else ""
        button.label = heart + suffix

    def update_play_button(self) -> None:
        self.query_one("#playpause", Button).label = "⏸" if self.player.paused else "▶"

    def update_time(self) -> None:
        duration = (self.current or {}).get("durationMs") or 0
        elapsed = min(self.player.elapsed(), duration / 1000 if duration else 0)
        bar = self.query_one("#progress", ProgressBar)
        bar.update(total=max(duration / 1000, 1), progress=elapsed)
        self.query_one("#time", Static).update(
            f"{fmt_duration(int(elapsed * 1000))} / {fmt_duration(duration)}"
        )

    # -- обложка ----------------------------------------------------------

    def show_placeholder_cover(self, text: str = "♪") -> None:
        self.query_one("#cover", Static).update(Text(text, justify="center"))

    def show_cover(self, url: str) -> None:
        data = self.cover_bytes.get(url)
        if not data:
            self.show_placeholder_cover()
            return
        try:
            self.query_one("#cover", Static).update(cover_renderable(data, self.cover_width))
        except (OSError, ValueError):
            self.show_placeholder_cover()

    def load_cover(self, track: dict) -> None:
        self.cover_token += 1
        token = self.cover_token
        url = track_cover_url(track)
        if not url:
            self.show_placeholder_cover()
            return
        if url in self.cover_bytes:
            self.show_cover(url)
            return
        self.show_placeholder_cover("загрузка…")
        self.fetch_cover(token, url)

    @work(thread=True, exclusive=True, group="cover")
    def fetch_cover(self, token: int, url: str) -> None:
        try:
            force_ipv4()
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            response.raise_for_status()
            data = response.content
        except (requests.RequestException, OSError):
            return
        self.call_from_thread(self.apply_cover, token, url, data)

    def apply_cover(self, token: int, url: str, data: bytes) -> None:
        if token != self.cover_token:
            return
        if len(self.cover_bytes) >= COVER_CACHE:
            self.cover_bytes.pop(next(iter(self.cover_bytes)))
        self.cover_bytes[url] = data
        self.show_cover(url)

    # -- воспроизведение --------------------------------------------------

    def play_from_list(self, tracks: list[dict], index: int, name: str) -> None:
        if not (0 <= index < len(tracks)):
            return
        self.queue_name = name
        self.set_topstatus(name)
        self.play_track(tracks[index], tracks, index)

    def play_track(
        self,
        track: dict,
        queue: list[dict] | None = None,
        index: int | None = None,
    ) -> None:
        if queue is not None:
            self.queue = queue
        if index is None:
            index = next(
                (
                    i
                    for i, item in enumerate(self.queue)
                    if str(item.get("id")) == str(track.get("id"))
                ),
                -1,
            )
        self.current = track
        self.current_index = index
        self.play_token += 1
        token = self.play_token
        self.update_now_playing()
        self.load_cover(track)
        self.set_topstatus(f"{self.queue_name} · загрузка…")

        self.fetch_stream(token, track)

    @work(thread=True, exclusive=False, group="stream")
    def fetch_stream(self, token: int, track: dict) -> None:
        api = self.api
        if not api:
            return
        try:
            url, item = resolve_stream(api, str(track.get("id")))
        except SystemExit as exc:
            self.call_from_thread(
                self.notify, f"Не удалось открыть трек: {exc}", severity="error"
            )
            return
        self.call_from_thread(
            self.start_playback, token, track, url, bool(item.get("preview"))
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
        self.history = record_history(track)
        self.update_play_button()
        self.set_topstatus(
            ("preview · " if preview else "") + (track.get("title") or "")
        )

    def action_toggle_pause(self) -> None:
        if not self.current:
            return
        if not self.player.is_playing():
            self.action_play_current()
            return
        self.player.toggle_pause()
        self.update_play_button()

    def action_play_current(self) -> None:
        if self.current:
            self.play_track(self.current, self.queue or self.wave, self.current_index)

    def try_next(self, direction: int) -> None:
        queue = self.queue or self.wave
        if not queue:
            return
        index = self.current_index + direction
        if index >= len(queue):
            if self.queue_name == "Моя волна":
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

    def action_seek_back(self) -> None:
        self.seek(-5)

    def action_seek_forward(self) -> None:
        self.seek(5)

    def seek(self, delta: float) -> None:
        if not self.current or not self.player.is_playing():
            return
        duration = (self.current.get("durationMs") or 0) / 1000
        self.run_worker(self._seek(delta, duration), group="seek", exclusive=True)

    async def _seek(self, delta: float, duration: float) -> None:
        await self.player.seek(delta, duration)
        self.update_time()

    async def seek_fraction(self, fraction: float) -> None:
        if not self.current or not self.player.is_playing():
            return
        duration = (self.current.get("durationMs") or 0) / 1000
        if duration <= 0:
            return
        target = max(0.0, min(duration - 1, duration * fraction))
        await self.player.seek(target - self.player.elapsed(), duration)
        self.update_time()

    @on(Click, "#progress")
    def progress_clicked(self, event: Click) -> None:
        width = event.widget.size.width or 1
        self.run_worker(self.seek_fraction(event.x / width), group="seek")

    # -- волна ------------------------------------------------------------

    @work(thread=True, exclusive=True, group="wave")
    def load_more_wave(self, then_play: int | None = None) -> None:
        api = self.api
        if not api:
            return
        try:
            from mtsmusic import fetch_mix_batch

            ids = [str(track.get("id")) for track in self.wave[-5:]]
            batch = fetch_mix_batch(api, ids or None)
            tracks = [
                item["track"]
                for item in (batch.get("sequence") or [])
                if item.get("type") == "track" and item.get("track")
            ]
            self.call_from_thread(self.apply_more_wave, tracks, then_play)
        except SystemExit as exc:
            self.call_from_thread(self.notify, f"Ошибка волны: {exc}", severity="error")

    def apply_more_wave(self, tracks: list[dict], then_play: int | None) -> None:
        known = {str(track.get("id")) for track in self.wave}
        added = [track for track in tracks if str(track.get("id")) not in known]
        self.wave.extend(added)
        self.set_topstatus(f"Волна · {len(self.wave)} · ♥ {len(self.liked_ids)}")
        if then_play is not None and then_play < len(self.queue):
            self.play_track(self.queue[then_play], self.queue, then_play)
        elif then_play is not None and added:
            self.queue = self.wave
            self.current_index = len(self.wave) - len(added)
            self.play_track(self.wave[self.current_index], self.wave, self.current_index)

    # -- избранное --------------------------------------------------------

    def action_toggle_like(self) -> None:
        if self.current:
            self.toggle_like(self.current)

    @work(thread=True, exclusive=True, group="like")
    def toggle_like(self, track: dict) -> None:
        api = self.api
        if not api:
            return
        track_id = str(track.get("id"))
        albums = track.get("albums") or []
        album = str(albums[0].get("id")) if albums else ""
        if not album:
            self.call_from_thread(self.notify, "У трека нет альбома", severity="error")
            return
        liked = track_id in self.liked_ids
        try:
            if liked:
                api.post(
                    f"/users/{self.uid}/likes/tracks/remove",
                    **{"track-ids": f"{track_id}:{album}"},
                )
            else:
                api.post(
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
        else:
            self.liked_ids.discard(track_id)
        if self.current and str(self.current.get("id")) == track_id:
            self.update_like_button(liked)
        self.set_topstatus(f"{self.queue_name} · ♥ {len(self.liked_ids)}")
        self.notify("♥ Добавлено" if liked else "♡ Убрано", timeout=2)

    # -- меню и экраны ----------------------------------------------------

    def action_menu(self) -> None:
        self.push_screen(MenuScreen(), self.menu_result)

    def menu_result(self, action: str | None) -> None:
        if action == "m-wave":
            self.push_screen(ListScreen("Моя волна", self.wave))
        elif action == "m-favorites":
            self.push_screen(FavoritesScreen())
        elif action == "m-history":
            self.push_screen(HistoryScreen(self.history))
        elif action == "m-search":
            self.push_screen(SearchScreen())
        elif action == "m-lyrics":
            self.action_show_lyrics()
        elif action == "m-quit":
            self.exit()

    def action_show_favorites(self) -> None:
        self.push_screen(FavoritesScreen())

    def action_show_wave(self) -> None:
        self.push_screen(ListScreen("Моя волна", self.wave))

    def action_show_history(self) -> None:
        self.push_screen(HistoryScreen(self.history))

    def action_show_search(self) -> None:
        self.push_screen(SearchScreen())

    def action_show_lyrics(self) -> None:
        if self.current:
            self.push_screen(LyricsScreen(self.current))
        else:
            self.notify("Сначала выбери трек", severity="warning")

    # -- события ----------------------------------------------------------

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "menu": self.action_menu,
            "prev": self.action_prev_track,
            "playpause": self.action_toggle_pause,
            "next": self.action_next_track,
            "lyrics": self.action_show_lyrics,
            "like": self.action_toggle_like,
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
            self.player.proc = None
            self.action_next_track()
            return
        self.update_time()
