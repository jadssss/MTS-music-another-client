# MTS Music CLI

Минимальный клиент для **своего** аккаунта MTS Music.

Внутри используется тот же путь, что и в веб-клиенте: MTS SSO выдаёт
OAuth-токен Яндекс.Музыки, дальше запросы идут на `api.music.yandex.net`.

## Установка

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright" .venv/bin/playwright install chromium
```

Всё локальное лежит рядом с проектом:
- `.venv/` — зависимости
- `.playwright/` — браузер Chromium
- `.mts-music/` — профиль браузера и `credentials.json`
