from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import InputMessagesFilterDocument, InputMessagesFilterVideo

from .config import Config
from .database import Database, utc_now
from .metadata import parse_metadata

logger = logging.getLogger(__name__)


def _index_progress_text(title: str, job: dict[str, Any]) -> str:
    total = job.get("files_total")
    total_label = str(total) if total is not None else "?"
    return (
        f"📈 OLD FILE INDEXING\n{title[:120]}\n"
        f"Status: {str(job.get('status', 'running')).upper()}\n"
        f"Files processed: {job.get('files_processed', 0)}/{total_label}\n"
        f"Indexed/updated: {job.get('indexed', 0)}\n"
        f"Skipped: {job.get('skipped', 0)}\n"
        f"Errors: {job.get('errors', 0)}"
        + (f"\nLast error: {str(job['error'])[:300]}" if job.get("error") else "")
    )


def _telethon_document(message: Any) -> dict[str, Any] | None:
    if not message.media:
        return None
    file = getattr(message, "file", None)
    if not file:
        return None
    if not (getattr(file, "name", None) or getattr(file, "mime_type", None)):
        return None
    return {
        "filename": getattr(file, "name", None) or "",
        "mime_type": getattr(file, "mime_type", None) or "",
        "size": getattr(file, "size", None),
    }


class HistoricalIndexer:
    def __init__(self, config: Config, db: Database, bot: Any):
        self.config = config
        self.db = db
        self.bot = bot
        self.client: TelegramClient | None = None
        self.error: str | None = None
        self.running_chats: set[str] = set()

    async def start(self) -> None:
        if not self.config.historical_indexing_enabled:
            return
        try:
            self.client = TelegramClient(
                StringSession(self.config.telethon_session or ""),
                self.config.telegram_api_id,
                self.config.telegram_api_hash,
                connection_retries=3,
                retry_delay=2,
                auto_reconnect=True,
            )
            await self.client.connect()
            if not await self.client.is_user_authorized():
                raise RuntimeError(
                    "TELETHON_SESSION is not authorized. Generate a valid Telegram user session before old indexing."
                )

            @self.client.on(events.NewMessage)
            async def on_new_message(event: Any) -> None:
                chat_id = str(event.chat_id)
                source = await self.db.sources.find_one({"chat_id": chat_id})
                if not source:
                    return
                await self.index_telethon_message(event.message, event.chat_id, source)

            logger.info("Historical indexing session connected.")
        except Exception as exc:
            self.error = str(exc)
            logger.exception("Historical indexing session could not connect.")
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    logger.exception("Could not close failed Telethon connection.")
            self.client = None

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()
            self.client = None

    async def index_telethon_message(self, message: Any, chat_id: int | str, source: dict[str, Any]) -> bool:
        file_data = _telethon_document(message)
        if not file_data:
            return False
        caption = message.message or ""
        metadata = parse_metadata(caption, file_data["filename"])
        doc = {
            **metadata,
            **file_data,
            "source_chat_id": str(chat_id),
            "source_message_id": int(message.id),
            "source_title": source.get("title", ""),
            "source_username": source.get("username"),
            "media_type": "video" if file_data["mime_type"].lower().startswith("video/") else "document",
            "bot_file_id": None,
        }
        await self.db.save_movie(doc)
        return True

    async def scan_chat(self, chat_id: int | str, admin_chat_id: int) -> None:
        if not self.config.historical_indexing_enabled:
            await self.bot.send_message(
                admin_chat_id,
                "Old-file indexing is unavailable. Set TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELETHON_SESSION in Render, then redeploy.",
            )
            return
        if not self.client or not self.client.is_connected():
            detail = self.error or "Telethon is not connected."
            await self.bot.send_message(admin_chat_id, f"Historical indexing is not ready: {detail}")
            return

        normalized_chat_id = str(chat_id)
        if normalized_chat_id in self.running_chats:
            await self.bot.send_message(admin_chat_id, "An indexing scan is already running for this source.")
            return

        self.running_chats.add(normalized_chat_id)
        job_id = f"{normalized_chat_id}:{int(datetime.now(timezone.utc).timestamp())}"
        started = utc_now()
        job: dict[str, Any] = {
            "chat_id": normalized_chat_id,
            "status": "running",
            "started_at": started,
            "files_total": None,
            "files_processed": 0,
            "indexed": 0,
            "skipped": 0,
            "errors": 0,
            "last_message_id": 0,
            "error": None,
        }
        progress_message_id: int | None = None
        source: dict[str, Any] = {"title": normalized_chat_id}
        try:
            source = await self.db.sources.find_one({"chat_id": normalized_chat_id})
            if not source:
                raise RuntimeError("This source is not registered in Index groups.")
            entity = await self.client.get_entity(int(normalized_chat_id))
            media_filters = (
                InputMessagesFilterDocument(),
                InputMessagesFilterVideo(),
            )
            media_totals = []
            for media_filter in media_filters:
                media_count = await self.client.get_messages(
                    entity,
                    limit=0,
                    filter=media_filter,
                )
                media_totals.append(
                    max(0, int(getattr(media_count, "total", 0) or 0))
                )
            job["files_total"] = sum(media_totals)
            await self.db.set_index_job(job_id, job)
            try:
                progress_message = await self.bot.send_message(
                    admin_chat_id,
                    _index_progress_text(source.get("title", normalized_chat_id), job),
                )
                progress_message_id = progress_message.message_id
            except Exception:
                logger.exception("Could not send historical-index progress message.")

            last_update = time.monotonic()
            for media_filter in media_filters:
                async for message in self.client.iter_messages(
                    entity,
                    reverse=True,
                    filter=media_filter,
                ):
                    job["files_processed"] += 1
                    job["last_message_id"] = int(message.id)
                    try:
                        if await self.index_telethon_message(message, normalized_chat_id, source):
                            job["indexed"] += 1
                        else:
                            job["skipped"] += 1
                    except Exception as exc:
                        job["errors"] += 1
                        job["error"] = str(exc)[:500]
                        raise

                    if job["files_processed"] % 25 == 0 or time.monotonic() - last_update >= 5:
                        job["updated_at"] = utc_now()
                        await self.db.set_index_job(job_id, job)
                        if progress_message_id:
                            try:
                                await self.bot.edit_message_text(
                                    chat_id=admin_chat_id,
                                    message_id=progress_message_id,
                                    text=_index_progress_text(source.get("title", normalized_chat_id), job),
                                )
                            except Exception:
                                logger.debug("Could not update historical-index progress message.", exc_info=True)
                        last_update = time.monotonic()

            job.update({"status": "completed", "finished_at": utc_now(), "updated_at": utc_now()})
            await self.db.set_index_job(
                job_id,
                job,
            )
            if progress_message_id:
                try:
                    await self.bot.edit_message_text(
                        chat_id=admin_chat_id,
                        message_id=progress_message_id,
                        text=_index_progress_text(source.get("title", normalized_chat_id), job),
                    )
                except Exception:
                    logger.debug("Could not publish completed historical-index summary.", exc_info=True)
        except Exception as exc:
            logger.exception("Historical indexing failed for source %s.", normalized_chat_id)
            job["status"] = "failed"
            if not job["errors"]:
                job["errors"] = 1
            job["error"] = str(exc)[:500]
            job["finished_at"] = utc_now()
            job["updated_at"] = utc_now()
            await self.db.set_index_job(
                job_id,
                job,
            )
            if progress_message_id:
                try:
                    await self.bot.edit_message_text(
                        chat_id=admin_chat_id,
                        message_id=progress_message_id,
                        text=_index_progress_text(source.get("title", normalized_chat_id), job),
                    )
                except Exception:
                    logger.debug("Could not publish failed historical-index summary.", exc_info=True)
            else:
                try:
                    await self.bot.send_message(admin_chat_id, _index_progress_text("Indexing", job))
                except Exception:
                    logger.exception("Could not send historical-index failure summary.")
        finally:
            self.running_chats.discard(normalized_chat_id)

