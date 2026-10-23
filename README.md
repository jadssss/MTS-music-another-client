# MTS Music CLI

Минимальный клиент для **своего** аккаунта MTS Music: поиск, ссылка на поток,
воспроизведение и скачивание в MP3.

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

## Вход

```bash
.venv/bin/python mtsmusic.py login
```

Откроется браузер. Нужно вручную войти в MTS и пройти капчу. Токен и cookie
сохранятся в `.mts-music/credentials.json` с правами `600`.
Повторный вход нужен только когда токен перестанет работать.

## Команды

```bash
# кто я и есть ли подписка
.venv/bin/python mtsmusic.py status

# поиск
.venv/bin/python mtsmusic.py search "miyagi" -n 10

# прямая ссылка на MP3
.venv/bin/python mtsmusic.py url 12345678

# воспроизведение (нужен mpv или ffplay)
.venv/bin/python mtsmusic.py play 12345678

# скачивание
.venv/bin/python mtsmusic.py download 12345678 -o track.mp3
```

Токен можно передать и через переменную окружения:

```bash
export MTS_YA_TOKEN='...'
```
