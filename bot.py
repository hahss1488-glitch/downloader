from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional, Tuple

from dotenv import load_dotenv
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

DEFAULT_MAX_VIDEO_MB = 50
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


def resolve_max_video_limit_bytes() -> tuple[int, int]:
    raw_value = os.getenv("MAX_VIDEO_MB", str(DEFAULT_MAX_VIDEO_MB))
    try:
        max_video_mb = max(1, int(raw_value))
    except ValueError:
        logging.warning(
            "Некорректный MAX_VIDEO_MB=%r, использую значение по умолчанию %s МБ",
            raw_value,
            DEFAULT_MAX_VIDEO_MB,
        )
        max_video_mb = DEFAULT_MAX_VIDEO_MB
    return max_video_mb, max_video_mb * 1024 * 1024


def setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )


def is_url_message(text: Optional[str]) -> bool:
    if not text:
        return False
    text = text.strip()
    return text.startswith("http://") or text.startswith("https://")




def _base_ydl_opts(output_template: str, max_filesize_bytes: int) -> dict[str, Any]:
    return {
        "outtmpl": output_template,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "extractor_retries": 3,
        "socket_timeout": 20,
        "max_filesize": max_filesize_bytes,
        # TikTok и часть CDN лучше отвечают при явном User-Agent
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            )
        },
    }


def _tiktok_fallback_ydl_opts(output_template: str, max_filesize_bytes: int) -> dict[str, Any]:
    opts = _base_ydl_opts(output_template, max_filesize_bytes)
    opts.update(
        {
            # Иногда комбинированные форматы для TikTok недоступны; пробуем проще
            "format": "best",
            "extractor_args": {
                # Часто помогает обойти нестабильность web-эндпоинтов TikTok
                "tiktok": {"api_hostname": ["api16-normal-c-useast1a.tiktokv.com"]}
            },
            "http_headers": {
                **opts["http_headers"],
                "Referer": "https://www.tiktok.com/",
            },
        }
    )
    return opts


def _find_downloaded_file(tmp_dir: Path, file_id: str) -> Path:
    candidates = sorted(
        tmp_dir.glob(f"{file_id}.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("Файл после скачивания не найден.")
    return candidates[0]

def _download_video_sync(url: str, tmp_dir: Path, max_filesize_bytes: int) -> Tuple[Path, int]:
    file_id = uuid.uuid4().hex
    output_template = str(tmp_dir / f"{file_id}.%(ext)s")

    # Первая попытка: универсальные настройки
    try:
        with YoutubeDL(_base_ydl_opts(output_template, max_filesize_bytes)) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError("yt-dlp не вернул информацию о видео.")
    except DownloadError:
        # Вторая попытка специально для TikTok
        if "tiktok.com" not in url.lower():
            raise

        logging.warning("Первая попытка для TikTok не удалась, запускаю fallback-режим")
        with YoutubeDL(_tiktok_fallback_ydl_opts(output_template, max_filesize_bytes)) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError("yt-dlp не вернул информацию о видео.")

    video_path = _find_downloaded_file(tmp_dir, file_id)
    size_bytes = video_path.stat().st_size
    return video_path, size_bytes


def cleanup_temp(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)
    except Exception:
        logging.exception("Не удалось удалить временные файлы: %s", path)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if not update.message:
        return
    await update.message.reply_text(
        "Привет! Отправь ссылку на видео (YouTube, TikTok, Instagram и др.), и я "
        "попробую скачать его и отправить в чат."
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if not update.message:
        return
    await update.message.reply_text(
        "Отправь ссылку вида http://... или https://...\n"
        "Я скачаю видео и отправлю его в чат.\n\n"
        f"Ограничение: файл должен быть не больше {context.application.bot_data.get('max_video_mb', DEFAULT_MAX_VIDEO_MB)} МБ."
    )


async def url_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    text = message.text.strip()
    if not is_url_message(text):
        await message.reply_text("Отправьте корректную ссылку, начиная с http:// или https://")
        return

    chat_id = message.chat_id
    logging.info("Получена ссылка от chat_id=%s: %s", chat_id, text)

    temp_dir = Path(tempfile.mkdtemp(prefix="tg_video_bot_"))
    downloaded_file: Optional[Path] = None

    try:
        await message.reply_text("Скачиваю видео, подождите...")

        loop = asyncio.get_running_loop()
        downloaded_file, file_size = await loop.run_in_executor(
            None,
            _download_video_sync,
            text,
            temp_dir,
            context.application.bot_data["max_video_bytes"],
        )

        ext = downloaded_file.suffix.lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            logging.warning("Нестандартный формат %s, пробую отправить как есть", ext)

        if file_size > context.application.bot_data["max_video_bytes"]:
            await message.reply_text(
                f"Видео больше {context.application.bot_data['max_video_mb']} МБ, Telegram-бот не может его отправить. "
                "Пришлите ссылку на видео меньшего размера."
            )
            return

        logging.info("Отправка файла %s в chat_id=%s", downloaded_file, chat_id)
        with downloaded_file.open("rb") as video_fp:
            await context.bot.send_video(chat_id=chat_id, video=video_fp, supports_streaming=True)

        await message.reply_text("Готово ✅")
        logging.info("Видео отправлено успешно")

    except DownloadError as exc:
        logging.exception("Ошибка yt-dlp")
        await message.reply_text(
            "Не удалось скачать видео по этой ссылке. Проверьте ссылку или попробуйте другую.\n"
            f"Детали: {exc}"
        )
    except TelegramError:
        logging.exception("Ошибка Telegram API")
        await message.reply_text(
            "Видео скачано, но Telegram не принял файл. Возможно, формат не поддерживается."
        )
    except Exception as exc:
        logging.exception("Непредвиденная ошибка")
        await message.reply_text(f"Произошла ошибка: {exc}")
    finally:
        cleanup_temp(temp_dir)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Глобальная ошибка. update=%s", update, exc_info=context.error)


def main() -> None:
    setup_logging()
    load_dotenv()

    max_video_mb, max_video_bytes = resolve_max_video_limit_bytes()
    logging.info("Лимит размера видео: %s МБ", max_video_mb)

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN. Добавьте его в переменные окружения или .env")

    app = ApplicationBuilder().token(token).build()
    app.bot_data["max_video_mb"] = max_video_mb
    app.bot_data["max_video_bytes"] = max_video_bytes
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, url_message_handler))
    app.add_error_handler(on_error)

    logging.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
