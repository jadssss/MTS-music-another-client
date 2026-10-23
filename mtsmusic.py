#!/usr/bin/env python3
"""MTS Music CLI — минимальный клиент для личного аккаунта.

Авторизация: MTS SSO через браузер (Playwright), затем запросы к
Яндекс.Музыке с полученным OAuth-токеном.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import random
import shutil
import socket
import string
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import requests

PROJECT_DIR = Path(__file__).resolve().parent

# Всё локальное храним рядом с проектом, а не в домашней директории.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PROJECT_DIR / ".playwright"))

CONFIG_DIR = Path(os.environ.get("MTSMUSIC_CONFIG_DIR", PROJECT_DIR / ".mts-music"))
CREDS_PATH = CONFIG_DIR / "credentials.json"
BROWSER_DIR = CONFIG_DIR / "browser"

AUTH_BASE = "https://api.music.mts.ru/auth"
BFF_BASE = "https://api.music.mts.ru"
YANDEX_BASE = "https://api.music.yandex.net"

MUSIC_CLIENT = "MTSMusicWebPremium/2.25.0-release-20046039"
# Константа из веб-клиента: подпись URL потока.
STREAM_SIGN_SECRET = "I5gMw9n41e7Ug2JYZV5Td6"
# Константа из веб-клиента: подпись запроса текста песни.
LYRICS_SIGN_KEY = "DBTX5BSsFvbhdajN0ahmza"

WAVE_STATION = "user:onyourwave"

USER_AGENT = "MTSMusicCLI/0.1 (+personal use)"

_orig_getaddrinfo = socket.getaddrinfo


def force_ipv4() -> None:
    """api.music.yandex.net иногда недоступен по IPv6 — ходим только по IPv4."""

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = getaddrinfo


def die(message: str, code: int = 1) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


def load_credentials() -> dict:
    if not CREDS_PATH.exists():
        return {}
    try:
        return json.loads(CREDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die(f"не удалось прочитать {CREDS_PATH}: {exc}")


def save_credentials(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        CREDS_PATH.chmod(0o600)
    except OSError:
        pass


def get_token() -> str | None:
    return os.environ.get("MTS_YA_TOKEN") or load_credentials().get("ya_token")


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


def make_state(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    rnd = random.SystemRandom()
    return "".join(rnd.choice(alphabet) for _ in range(length))


def get_auth_settings(reference_uri: str = "https://music.mts.ru/") -> dict:
    force_ipv4()
    response = requests.get(
        f"{AUTH_BASE}/v1/web/auth_settings",
        params={"reference_uri": reference_uri, "state": make_state()},
        headers={
            "x-yandex-music-client": MUSIC_CLIENT,
            "Origin": "https://music.mts.ru",
            "Referer": "https://music.mts.ru/",
            "User-Agent": USER_AGENT,
        },
        timeout=30,
    )
    if response.status_code != 200:
        die(f"auth_settings вернул HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


def cmd_login(args: argparse.Namespace) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        die("не установлен playwright. Выполни: pip install -r requirements.txt")

    settings = get_auth_settings()
    authorize_uri = settings["web_sso_authorize_uri"]
    print("Открываю браузер. Войди в аккаунт MTS (телефон + SMS/пароль).")
    print("Капчу нужно пройти вручную. Окно закроется само после входа.\n")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(BROWSER_DIR),
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(authorize_uri, wait_until="domcontentloaded")

            deadline = time.time() + args.timeout
            token = None
            while time.time() < deadline:
                try:
                    token = page.evaluate("localStorage.getItem('ya_token')")
                except Exception:
                    token = None
                if token:
                    break
                # Токен может прийти в URL до того, как SPA успеет его сохранить.
                if "yatoken=" in page.url:
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
                    token = (query.get("yatoken") or [None])[0]
                    if token:
                        break
                page.wait_for_timeout(1000)

            if not token:
                die("не дождался токена. Вход не завершён или истекло время ожидания.")

            ya_uid = page.evaluate("localStorage.getItem('ya_uid')")
            user_id = page.evaluate("localStorage.getItem('userId')")
            cookies = context.cookies()

            data = {
                "ya_token": token,
                "ya_uid": ya_uid,
                "user_id": user_id,
                "cookies": cookies,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            save_credentials(data)
            print(f"\nТокен сохранён: {CREDS_PATH} (chmod 600)")
            if ya_uid:
                print(f"Yandex UID: {ya_uid}")
        finally:
            context.close()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class Api:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or get_token()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Yandex-Music-Client": MUSIC_CLIENT,
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )

    def _headers(self) -> dict:
        if self.token:
            return {"Authorization": f"OAuth {self.token}"}
        return {}

    def get(self, path: str, **params) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, **params) -> dict:
        return self._request("POST", path, params=params)

    def _request(self, method: str, path: str, params: dict | None = None) -> dict:
        force_ipv4()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.request(
                    method,
                    f"{YANDEX_BASE}{path}",
                    params=params or None,
                    headers=self._headers(),
                    timeout=30,
                )
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        else:
            die(f"сетевая ошибка на {path}: {last_error}")
            raise AssertionError  # для type checker

        if response.status_code == 401:
            die("токен не принят (401). Выполни заново: python mtsmusic.py login")
        if response.status_code == 404:
            die(f"не найдено (404) на {path}")
        if response.status_code != 200:
            die(f"HTTP {response.status_code} на {path}: {response.text[:300]}")
        return response.json()

    def get_url(self, url: str) -> dict:
        force_ipv4()
        response = self.session.get(url, headers=self._headers(), timeout=30)
        if response.status_code != 200:
            die(f"HTTP {response.status_code} на {url[:120]}: {response.text[:300]}")
        return response.json()


def require_token(api: Api, what: str) -> None:
    if not api.token:
        die(f"для «{what}» нужен токен. Сначала: python mtsmusic.py login")


def bff_get(path: str) -> dict | None:
    """Запрос к BFF MTS с сохранёнными cookie. None, если cookie нет/не подошли."""
    cookies = load_credentials().get("cookies") or []
    if not cookies:
        return None

    session = requests.Session()
    for cookie in cookies:
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )
    force_ipv4()
    try:
        response = session.get(
            f"{BFF_BASE}{path}",
            headers={
                "X-Music-Client": "web",
                "X-Yandex-Music-Client": MUSIC_CLIENT,
                "Origin": "https://music.mts.ru",
                "Referer": "https://music.mts.ru/",
                "User-Agent": USER_AGENT,
            },
            timeout=30,
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    return response.json()


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


def fmt_duration(ms: int | None) -> str:
    total = int((ms or 0) / 1000)
    return f"{total // 60}:{total % 60:02d}"


def fmt_track(track: dict) -> str:
    artists = ", ".join(a.get("name", "?") for a in track.get("artists") or [])
    title = track.get("title") or "?"
    available = "" if track.get("available", True) else " [недоступен]"
    return (
        f"{str(track.get('id', '?')):>12}  "
        f"{artists} — {title}  "
        f"[{fmt_duration(track.get('durationMs'))}]{available}"
    )


def cmd_search(args: argparse.Namespace) -> None:
    api = Api()
    payload = api.get(
        "/search",
        text=args.query,
        type="all",
        page=0,
        **{"page-size": args.limit},
        nocorrect="true",
    )
    result = payload.get("result") or {}
    tracks = ((result.get("tracks") or {}).get("results")) or []
    if not tracks:
        best = (result.get("best") or {}).get("result")
        if isinstance(best, dict) and best.get("id"):
            print(fmt_track(best))
            return
        print("Ничего не найдено.")
        return
    for track in tracks[: args.limit]:
        print(fmt_track(track))


def cmd_status(args: argparse.Namespace) -> None:
    api = Api()
    require_token(api, "статус")
    payload = api.get("/account/mts/status")
    result = payload.get("result") or {}
    account = result.get("account") or {}
    subscriptions = ((result.get("masterhub") or {}).get("activeSubscriptions")) or []

    # BFF даёт человекочитаемое название тарифа.
    bff_profile = bff_get("/v1/user-profile") or {}
    profile = bff_profile.get("profile") or {}
    bff_subscriptions = bff_profile.get("subscriptions") or []

    print(f"UID:          {account.get('uid', '?')}")
    print(f"Имя:          {profile.get('alias') or account.get('fullName') or account.get('displayName') or account.get('login') or '?'}")
    print(f"Телефон:      {profile.get('phone') or account.get('phone') or '?'}")
    print(f"Подписка:     {'активна' if subscriptions or bff_subscriptions else 'нет'}")
    if bff_subscriptions:
        for sub in bff_subscriptions:
            name = sub.get("name") or sub.get("id") or "?"
            until = sub.get("nextTarifficationDate")
            print(f"  - {name}{f' (до {until})' if until else ''}")
    else:
        for sub in subscriptions:
            name = sub.get("name") or sub.get("productName") or sub.get("subscriptionId") or sub.get("id")
            until = sub.get("expires") or sub.get("expirationDate")
            print(f"  - {name}{f' (до {until})' if until else ''}")

    # Дописываем uid в креды, если при логине SPA не успела его сохранить.
    creds = load_credentials()
    if creds and creds.get("ya_token") == api.token and not creds.get("ya_uid"):
        creds["ya_uid"] = str(account.get("uid") or "")
        save_credentials(creds)


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------


def resolve_stream(api: Api, track_id: str) -> tuple[str, dict]:
    """Возвращает (url_mp3, download_info_item)."""
    info = api.get(f"/tracks/{track_id}/download-info")
    items = info.get("result") or []
    if not items:
        die(f"для трека {track_id} нет download-info (возможно, недоступен)")

    # Лучший вариант: не preview и максимальный битрейт.
    items.sort(
        key=lambda item: (not item.get("preview", False), item.get("bitrateInKbps", 0)),
        reverse=True,
    )
    item = items[0]

    download_url = item["downloadInfoUrl"]
    parsed = urllib.parse.urlparse(download_url)
    query = parsed.query + ("&" if parsed.query else "") + "format=json"

    meta = api.get_url(f"{YANDEX_BASE}{parsed.path}?{query}")
    path = meta["path"]
    signature = meta["s"]
    host = meta["host"]
    timestamp = meta["ts"]

    digest = hashlib.md5((STREAM_SIGN_SECRET + path + signature).encode()).hexdigest()
    stream_url = f"https://{host}/get-mp3/{digest}/{timestamp}{path}"
    return stream_url, item


def cmd_url(args: argparse.Namespace) -> None:
    api = Api()
    url, item = resolve_stream(api, args.track_id)
    if item.get("preview"):
        print("# preview: полный трек недоступен без активной подписки", file=sys.stderr)
    print(url)


def pick_player(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for candidate in ("mpv", "ffplay"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def print_track_info(api: Api, track_id: str) -> None:
    try:
        track = api.get(f"/tracks/{track_id}")["result"][0]
    except (SystemExit, KeyError, IndexError):
        return
    print(fmt_track(track), file=sys.stderr)


def play_url(player: str, url: str) -> None:
    name = Path(player).name
    if name == "ffplay":
        command = [player, "-nodisp", "-autoexit", "-loglevel", "warning", url]
    else:
        command = [player, "--no-video", "--really-quiet", url]
    try:
        subprocess.run(command, check=False)
    except KeyboardInterrupt:
        raise


def play_items(api: Api, tracks: list[dict], explicit_player: str | None = None) -> None:
    player = pick_player(explicit_player)
    if not player:
        die("не найден mpv/ffplay. Установи один из них или используй: mtsmusic.py url <id>")

    for index, track in enumerate(tracks, 1):
        track_id = str(track.get("id"))
        print(f"\n▶ {index}/{len(tracks)}  {fmt_track(track)}", file=sys.stderr)
        try:
            url, item = resolve_stream(api, track_id)
        except SystemExit:
            print("  пропускаю: не удалось получить поток", file=sys.stderr)
            continue
        if item.get("preview"):
            print("  внимание: preview-фрагмент", file=sys.stderr)
        try:
            play_url(player, url)
        except KeyboardInterrupt:
            print("\nОстановлено.", file=sys.stderr)
            return


def cmd_play(args: argparse.Namespace) -> None:
    api = Api()
    print_track_info(api, args.track_id)
    url, item = resolve_stream(api, args.track_id)
    if item.get("preview"):
        print("Внимание: это preview-фрагмент (нет активной подписки).", file=sys.stderr)

    player = pick_player(args.player)
    if not player:
        die("не найден mpv/ffplay. Установи один из них или используй: mtsmusic.py url <id>")

    try:
        play_url(player, url)
    except KeyboardInterrupt:
        pass


def cmd_download(args: argparse.Namespace) -> None:
    api = Api()
    print_track_info(api, args.track_id)
    url, _ = resolve_stream(api, args.track_id)
    force_ipv4()
    output = Path(args.output or f"{args.track_id}.mp3")

    with requests.get(url, stream=True, headers={"User-Agent": USER_AGENT}, timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with output.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r{done * 100 // total:3d}%  {done // 1024} KiB", end="", file=sys.stderr)
        print(file=sys.stderr)
    print(f"Сохранено: {output} ({output.stat().st_size} байт)")


# ---------------------------------------------------------------------------
# cli
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtsmusic",
        description="Минимальный клиент MTS Music для личного аккаунта.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="войти через браузер и сохранить токен")
    login.add_argument("--headless", action="store_true", help="без окна браузера (капчу не пройти)")
    login.add_argument("--timeout", type=int, default=600, help="секунд на вход (по умолчанию 600)")
    login.set_defaults(func=cmd_login)

    status = sub.add_parser("status", help="показать аккаунт и подписку")
    status.set_defaults(func=cmd_status)

    search = sub.add_parser("search", help="поиск треков")
    search.add_argument("query", help="поисковый запрос")
    search.add_argument("-n", "--limit", type=int, default=10, help="сколько результатов (по умолчанию 10)")
    search.set_defaults(func=cmd_search)

    url = sub.add_parser("url", help="получить прямую ссылку на MP3")
    url.add_argument("track_id")
    url.set_defaults(func=cmd_url)

    play = sub.add_parser("play", help="воспроизвести трек через mpv/ffplay")
    play.add_argument("track_id")
    play.add_argument("--player", help="путь к mpv/ffplay")
    play.set_defaults(func=cmd_play)

    download = sub.add_parser("download", help="скачать трек в MP3")
    download.add_argument("track_id")
    download.add_argument("-o", "--output", help="имя файла")
    download.set_defaults(func=cmd_download)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except requests.RequestException as exc:
        die(f"сетевая ошибка: {exc}")


if __name__ == "__main__":
    main()
