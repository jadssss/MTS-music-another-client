# MTS Music CLI

Минимальный клиент для **своего** аккаунта MTS Music: «Моя волна», поиск,
избранное, тексты песен, воспроизведение и скачивание в MP3.

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

## TUI-плеер

```bash
.venv/bin/python mtsmusic.py tui
```

Интерфейс в терминале сделан как экран телефона: по центру обложка (цветными
блоками, нужен truecolor-терминал), ниже — большая кнопка play/pause, по бокам
— предыдущий и следующий треки. Под ними — «Текст» и «В избранное». Кнопка
«☰» в левом верхнем углу открывает боковое меню: «Моя волна», избранное,
история прослушивания, поиск, текст песни и выход.

Мышь работает: кнопки, меню, строки списков и полоса прогресса кликабельны
(клик по прогрессу перематывает трек).

| Клавиша | Действие |
|---------|----------|
| `Space` | пауза / продолжить |
| `←` / `→` | перемотка на −5 / +5 секунд |
| `n` / `p` | следующий / предыдущий трек |
| `t` | текст песни |
| `l` | добавить / убрать из избранного |
| `m` | открыть меню |
| `w` | список «Моей волны» |
| `f` | избранное |
| `h` | история |
| `s` | поиск |
| `q` | выход |

История хранится в `.mts-music/history.json` (последние 100 треков).
Плеер — `ffplay` (или `mpv`, если установлен). Пауза реализована через
`SIGSTOP`/`SIGCONT`, а перемотка перезапускает поток с нужной секунды,
поэтому отдельный демон не нужен.

## Команды

```bash
# кто я и есть ли подписка
.venv/bin/python mtsmusic.py status

# поиск
.venv/bin/python mtsmusic.py search "miyagi" -n 10

# «Моя волна»: 10 треков, ♥ — уже в избранном
.venv/bin/python mtsmusic.py mix -n 10

# сразу слушать подборку (Ctrl-C — остановить)
.venv/bin/python mtsmusic.py mix -n 5 --play

# избранное: добавить / убрать / показать
.venv/bin/python mtsmusic.py like 12345678
.venv/bin/python mtsmusic.py unlike 12345678
.venv/bin/python mtsmusic.py likes -n 20

# текст песни: показать, сохранить, или с таймкодами
.venv/bin/python mtsmusic.py lyrics 12345678
.venv/bin/python mtsmusic.py lyrics 12345678 -o track.txt
.venv/bin/python mtsmusic.py lyrics 12345678 --lrc -o track.lrc

# только права MTS, без внешнего источника
.venv/bin/python mtsmusic.py lyrics 12345678 --no-fallback

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

## Если TUI не отображается

```bash
# диагностика окружения
.venv/bin/python mtsmusic.py tui --check

# без alternate screen (если экран пустой)
.venv/bin/python mtsmusic.py tui --inline

# отключить мышь, если терминал плохо её поддерживает
.venv/bin/python mtsmusic.py tui --inline --no-mouse
```

TUI нужно запускать в настоящем терминале (stdin/stdout — TTY), а не в панели
вывода IDE. При падении traceback пишется в `.mts-music/tui-error.log`.

## Ограничения

- Логин автоматизирован не полностью: QRATOR-капчу проходит человек.
- `reference_uri` у MTS должен быть на домене `music.mts.ru`, поэтому
  локальный callback не используется — токен забирается из браузера.
- Без активной подписки `download-info` отдаёт `preview` (короткий фрагмент).
- Тексты песен — отдельная подписка Яндекс Плюс, которой у MTS-аккаунта нет
  (`hasPlus: false`), поэтому MTS возвращает `404` даже для треков с текстом.
  `lyrics` автоматически берёт текст из бесплатного [LRCLIB](https://lrclib.net)
  и помечает источник в выводе. `--no-fallback` отключает это поведение.
- Использовать только для личного прослушивания, не перераздавать контент.
- Если `api.music.yandex.net` недоступен, проверь IPv6: клиент принудительно
  ходит по IPv4.
