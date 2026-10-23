"""Shared track helpers and local history storage."""

from __future__ import annotations

import json
from pathlib import Path

from mtsmusic import CONFIG_DIR

HISTORY_PATH = CONFIG_DIR / "history.json"
HISTORY_LIMIT = 100


def track_artist(track: dict) -> str:
    return ", ".join(a.get("name", "?") for a in track.get("artists") or []) or "?"


def track_album(track: dict) -> str:
    albums = track.get("albums") or []
    return (albums[0].get("title") or "—") if albums else "—"


def track_cover_url(track: dict, size: int = 360) -> str | None:
    uri = track.get("coverUri")
    if not uri:
        albums = track.get("albums") or []
        uri = albums[0].get("coverUri") if albums else None
    if not uri:
        return None
    uri = uri.replace("%%", f"{size}x{size}")
    if not uri.startswith("http"):
        uri = "https://" + uri
    return uri


def load_history() -> list[dict]:
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    seen: set[str] = set()
    history: list[dict] = []
    for track in data:
        if not isinstance(track, dict):
            continue
        track_id = str(track.get("id") or "")
        if not track_id or track_id in seen:
            continue
        seen.add(track_id)
        history.append(track)
    return history[:HISTORY_LIMIT]


def record_history(track: dict) -> list[dict]:
    """Добавляет трек в начало истории и сохраняет последние 100."""
    track_id = str(track.get("id") or "")
    if not track_id:
        return load_history()
    history = [item for item in load_history() if str(item.get("id")) != track_id]
    history.insert(0, track)
    history = history[:HISTORY_LIMIT]
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(
            json.dumps(history, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError:
        pass
    return history
