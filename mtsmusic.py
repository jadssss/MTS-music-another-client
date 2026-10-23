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
# моя волна, избранное, тексты
# ---------------------------------------------------------------------------


def get_uid(api: Api) -> str:
    uid = (load_credentials() or {}).get("ya_uid")
    if uid:
        return str(uid)
    # Резервный путь: uid есть в ответе MTS-статуса.
    result = api.get("/account/mts/status").get("result") or {}
    uid = (result.get("account") or {}).get("uid")
    if not uid:
        die("не удалось определить uid аккаунта. Выполни: python mtsmusic.py login")
    creds = load_credentials() or {}
    creds["ya_uid"] = str(uid)
    save_credentials(creds)
    return str(uid)


def get_track(api: Api, track_id: str) -> dict:
    result = api.get(f"/tracks/{track_id}").get("result") or []
    if not result:
        die(f"трек {track_id} не найден")
    return result[0]


def album_id(track: dict) -> str | None:
    albums = track.get("albums") or []
    if albums:
        return str(albums[0].get("id"))
    return None


def track_album_ref(track_id: str, album: str | None = None) -> str:
    return f"{track_id}:{album}" if album else str(track_id)


def fetch_mix_batch(api: Api, queue: list[str] | None = None) -> dict:
    params = {"queue": ",".join(queue)} if queue else {}
    return api.get(f"/rotor/station/{WAVE_STATION}/tracks", **params).get("result") or {}


def print_mix_item(index: int, item: dict) -> None:
    track = item.get("track") or {}
    heart = "♥" if item.get("liked") else " "
    print(f"{heart} {index:>3}. {fmt_track(track)}", flush=True)


def cmd_mix(args: argparse.Namespace) -> None:
    api = Api()
    require_token(api, "моя волна")

    limit = max(1, args.number)
    collected: list[dict] = []
    seen: set[str] = set()
    queue: list[str] | None = None
    attempts = 0
    max_attempts = max(10, limit)

    print(f"Моя волна — {limit} трек(ов):", file=sys.stderr)
    while len(collected) < limit and attempts < max_attempts:
        attempts += 1
        result = fetch_mix_batch(api, queue)
        sequence = result.get("sequence") or []
        if not sequence:
            break

        new_items = []
        for item in sequence:
            if item.get("type") != "track":
                continue
            track_id = str((item.get("track") or {}).get("id") or "")
            if not track_id or track_id in seen:
                continue
            seen.add(track_id)
            new_items.append(item)

        if not new_items:
            break

        for item in new_items:
            if len(collected) >= limit:
                break
            collected.append(item)

        ids = [str((item.get("track") or {}).get("id")) for item in sequence]
        queue = ids[-5:]

    if not collected:
        die("волна вернула пустой список")

    for index, item in enumerate(collected, 1):
        print_mix_item(index, item)

    if args.play:
        play_items(api, [item.get("track") or {} for item in collected], args.player)


def cmd_like(args: argparse.Namespace) -> None:
    api = Api()
    require_token(api, "избранное")
    uid = get_uid(api)
    track = get_track(api, args.track_id)
    album = args.album or album_id(track)
    ref = track_album_ref(args.track_id, album)
    api.post(f"/users/{uid}/likes/tracks/add", **{"track-id": ref})
    print(f"♥ добавлено в избранное: {fmt_track(track)}")


def cmd_unlike(args: argparse.Namespace) -> None:
    api = Api()
    require_token(api, "избранное")
    uid = get_uid(api)
    track = get_track(api, args.track_id)
    album = args.album or album_id(track)
    ref = track_album_ref(args.track_id, album)
    api.post(f"/users/{uid}/likes/tracks/remove", **{"track-ids": ref})
    print(f"♡ убрано из избранного: {fmt_track(track)}")


def cmd_likes(args: argparse.Namespace) -> None:
    api = Api()
    require_token(api, "избранное")
    uid = get_uid(api)
    payload = api.get(f"/users/{uid}/likes/tracks")
    library = (payload.get("result") or {}).get("library") or {}
    liked = library.get("tracks") or []
    if not liked:
        print("В избранном пока пусто.")
        return

    limit = max(1, args.number)
    liked = liked[:limit]
    refs = ",".join(str(item.get("id")) for item in liked)

    details: dict[str, dict] = {}
    # /tracks отдаёт не больше сотни id за раз — бьём на порции.
    ids = [str(item.get("id")) for item in liked]
    for start in range(0, len(ids), 50):
        chunk = ids[start : start + 50]
        try:
            tracks = api.get("/tracks", trackIds=",".join(chunk)).get("result") or []
        except SystemExit:
            tracks = []
        for track in tracks:
            details[str(track.get("id"))] = track

    for index, item in enumerate(liked, 1):
        track_id = str(item.get("id"))
        track = details.get(track_id)
        if track:
            print(f"{index:>3}. {fmt_track(track)}")
        else:
            album = item.get("albumId")
            print(f"{index:>3}. {track_id:>12}  (альбом {album})")


def lyrics_response(api: Api, track_id: str, fmt: str = "TEXT") -> requests.Response:
    """Запрос текста с подписью из веб-клиента MTS."""
    timestamp = int(time.time())
    message = f"{track_id}{timestamp}"
    signature = base64.b64encode(
        hmac.new(LYRICS_SIGN_KEY.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()

    force_ipv4()
    return api.session.get(
        f"{YANDEX_BASE}/tracks/{track_id}/lyrics",
        params={"timeStamp": timestamp, "sign": signature, "format": fmt},
        headers=api._headers(),
        timeout=30,
    )


def lyrics_text(api: Api, response: requests.Response) -> str:
    """Достаёт сам текст: API отдаёт ссылку на файл, а не строки."""
    payload = response.json()
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return ""

    for key in ("lyrics", "full_lyrics", "fullLyrics", "text"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value

    download_url = result.get("download_url") or result.get("downloadUrl")
    if download_url:
        force_ipv4()
        file_response = requests.get(
            download_url, headers={"User-Agent": USER_AGENT}, timeout=60
        )
        file_response.raise_for_status()
        return file_response.text
    return ""


LRCLIB_URL = "https://lrclib.net/api/search"


def lrclib_lyrics(track: dict, want_sync: bool) -> str | None:
    """Резервный источник текста: LRCLIB (бесплатный API, без ключа)."""
    title = (track.get("title") or "").strip()
    artists = track.get("artists") or []
    artist = (artists[0].get("name") or "").strip() if artists else ""
    if not title:
        return None

    force_ipv4()
    headers = {"User-Agent": "MTSMusicCLI/0.1 (personal use)"}
    queries = [
        {"track_name": title, "artist_name": artist},
        {"q": f"{artist} {title}".strip()},
    ]
    candidates: list[dict] = []
    for params in queries:
        try:
            response = requests.get(LRCLIB_URL, params=params, headers=headers, timeout=20)
        except requests.RequestException:
            continue
        if response.status_code != 200:
            continue
        try:
            data = response.json()
        except ValueError:
            continue
        if isinstance(data, list) and data:
            candidates = data
            break
    if not candidates:
        return None

    # Выбираем версию, ближайшую по длительности, где есть нужный формат.
    field = "syncedLyrics" if want_sync else "plainLyrics"
    duration = (track.get("durationMs") or 0) / 1000

    def score(item: dict) -> float:
        diff = abs(float(item.get("duration") or 0) - duration) if duration else 0
        exact = 0 if (item.get("trackName") or "").casefold() == title.casefold() else 50
        return diff + exact

    usable = [item for item in candidates if (item.get(field) or "").strip()]
    if not usable and want_sync:
        usable = [item for item in candidates if (item.get("plainLyrics") or "").strip()]
        field = "plainLyrics"
    if not usable:
        return None
    best = min(usable, key=score)
    return (best.get(field) or "").strip() or None


def cmd_lyrics(args: argparse.Namespace) -> None:
    api = Api()
    require_token(api, "текст песни")
    track = get_track(api, args.track_id)
    fmt = "LRC" if args.lrc else "TEXT"

    text = ""
    source = "MTS"
    response = lyrics_response(api, args.track_id, fmt)
    if response.status_code == 200:
        try:
            text = lyrics_text(api, response)
        except (ValueError, requests.RequestException):
            text = ""
        if "Подключите подписку" in text:
            text = ""
    elif response.status_code not in (403, 404):
        die(f"HTTP {response.status_code}: {response.text[:300]}")

    if not text.strip() and not args.no_fallback:
        text = lrclib_lyrics(track, want_sync=args.lrc) or ""
        source = "LRCLIB"

    if not text.strip():
        print(f"Текст не найден: {fmt_track(track)}")
        return

    if args.output:
        output = Path(args.output)
        output.write_text(text, encoding="utf-8")
        print(f"Сохранено: {output} ({len(text)} символов, источник: {source})")
    else:
        if source != "MTS":
            print(f"# источник текста: {source}", file=sys.stderr)
        print(f"# {fmt_track(track)}")
        print()
        print(text)


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

    mix = sub.add_parser("mix", help="«Моя волна»: подборка треков")
    mix.add_argument("-n", "--number", type=int, default=10, help="сколько треков (по умолчанию 10)")
    mix.add_argument("--play", action="store_true", help="сразу воспроизвести подборку")
    mix.add_argument("--player", help="путь к mpv/ffplay")
    mix.set_defaults(func=cmd_mix)

    like = sub.add_parser("like", help="добавить трек в избранное")
    like.add_argument("track_id")
    like.add_argument("--album", help="id альбома (если нужно переопределить)")
    like.set_defaults(func=cmd_like)

    unlike = sub.add_parser("unlike", help="убрать трек из избранного")
    unlike.add_argument("track_id")
    unlike.add_argument("--album", help="id альбома (если нужно переопределить)")
    unlike.set_defaults(func=cmd_unlike)

    likes = sub.add_parser("likes", help="показать избранные треки")
    likes.add_argument("-n", "--number", type=int, default=50, help="сколько показать (по умолчанию 50)")
    likes.set_defaults(func=cmd_likes)

    lyrics = sub.add_parser("lyrics", help="показать или сохранить текст песни")
    lyrics.add_argument("track_id")
    lyrics.add_argument("--lrc", action="store_true", help="формат LRC с таймкодами")
    lyrics.add_argument("-o", "--output", help="сохранить текст в файл")
    lyrics.add_argument(
        "--no-fallback",
        action="store_true",
        help="не использовать LRCLIB, если MTS не отдал текст",
    )
    lyrics.set_defaults(func=cmd_lyrics)

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
