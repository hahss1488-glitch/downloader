# Telegram Video Downloader Bot

Бот принимает ссылку (`http://` / `https://`), скачивает видео через `yt-dlp` и отправляет его в Telegram.
Ограничение: до **50 МБ**.

## Что в репозитории

- `bot.py` — основной код бота
- `requirements.txt` — зависимости Python
- `.gitignore` — игнор служебных файлов

## Короткая инструкция для сервера

### 1) Куда закидывать файлы

Рекомендуемый путь на сервере:

```bash
/opt/tg-video-bot/
```

Склонируйте репозиторий именно туда (или в любую другую папку, но далее используйте её в командах):

```bash
git clone <URL_ВАШЕГО_РЕПО> /opt/tg-video-bot
cd /opt/tg-video-bot
```

### 2) Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3) Токен (без публикации в GitHub)

На сервере создайте файл `.env` в корне проекта `/opt/tg-video-bot/.env`:

```env
BOT_TOKEN=123456789:AA...ваш_токен...
```

`.env` уже игнорируется через `.gitignore`, в репозиторий он не попадёт.

### 4) Запуск

```bash
cd /opt/tg-video-bot
source .venv/bin/activate
python bot.py
```

## Важно

- Для части сайтов может понадобиться `ffmpeg` в системе.
- Если видео больше 50 МБ, бот попросит прислать ссылку на файл меньшего размера.
