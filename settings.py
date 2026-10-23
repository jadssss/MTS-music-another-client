"""Настройки MTS Music: хранятся в .mts-music/settings.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mtsmusic import CONFIG_DIR

SETTINGS_PATH = CONFIG_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    "volume": 80,            # громкость 0..100
    "seek_step": 5,          # шаг перемотки стрелками, секунды
    "autoplay": False,       # запускать воспроизведение сразу
    "start_queue": "wave",   # wave | favorites | history
    "wave_from_history": True,  # продолжать волну с последних треков
}

START_QUEUES = ("wave", "favorites", "history")
START_QUEUE_LABELS = {
    "wave": "Моя волна",
    "favorites": "Избранное",
    "history": "История",
}

_cache: dict[str, Any] | None = None


def _coerce(key: str, value: Any) -> Any:
    if key == "volume":
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return DEFAULTS[key]
    if key == "seek_step":
        try:
            return max(1, min(60, int(value)))
        except (TypeError, ValueError):
            return DEFAULTS[key]
    if key in ("autoplay", "wave_from_history"):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "да", "вкл")
    if key == "start_queue":
        text = str(value)
        return text if text in START_QUEUES else DEFAULTS[key]
    return value


def load_settings() -> dict[str, Any]:
    """Возвращает настройки с дефолтами; файл читается один раз."""
    global _cache
    if _cache is not None:
        return dict(_cache)
    raw: dict[str, Any] = {}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            raw = data
    except (OSError, ValueError):
        raw = {}
    merged = dict(DEFAULTS)
    for key, value in raw.items():
        if key in DEFAULTS:
            merged[key] = _coerce(key, value)
    _cache = merged
    return dict(merged)


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Сохраняет изменённые настройки и возвращает актуальный набор."""
    global _cache
    merged = load_settings()
    for key, value in values.items():
        if key in DEFAULTS:
            merged[key] = _coerce(key, value)
    _cache = merged
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return dict(merged)


def get_setting(key: str, default: Any = None) -> Any:
    return load_settings().get(key, DEFAULTS.get(key, default))


def set_setting(key: str, value: Any) -> Any:
    return save_settings({key: value}).get(key)


def update_settings(**values: Any) -> dict[str, Any]:
    return save_settings(dict(values))


def settings_path() -> Path:
    return SETTINGS_PATH
