from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from .config import Config
from .database import Database, utc_now
from .metadata import parse_metadata

logger = logging.getLogger(__name__)


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
            "media_type": "document",
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
        await self.db.set_index_job(
            job_id,
            {
                "chat_id": normalized_chat_id,
                "status": "running",
                "started_at": started,
                "indexed": 0,
                "last_message_id": 0,
                "error": None,
            },
        )
        count = 0
        try:
            source = await self.db.sources.find_one({"chat_id": normalized_chat_id})
            if not source:
                raise RuntimeError("This source is not registered in Index groups.")
            entity = await self.client.get_entity(int(normalized_chat_id))
            await self.bot.send_message(
                admin_chat_id,
                f"Old-file indexing started for {source.get('title', normalized_chat_id)}. Existing index records will be updated, not cleared.",
            )
            async for message in self.client.iter_messages(entity, reverse=True):
                if not message.media:
                    continue
                if await self.index_telethon_message(message, normalized_chat_id, source):
                    count += 1
                    if count % 100 == 0:
                        await self.db.set_index_job(
                            job_id,
                            {"indexed": count, "last_message_id": int(message.id), "updated_at": utc_now()},
                        )
                        await self.bot.send_message(admin_chat_id, f"Indexing progress: {count} files indexed.")
            await self.db.set_index_job(
                job_id,
                {
                    "status": "completed",
                    "indexed": count,
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                },
            )
            await self.bot.send_message(admin_chat_id, f"Historical indexing finished. {count} files added or refreshed.")
        except Exception as exc:
            logger.exception("Historical indexing failed for source %s.", normalized_chat_id)
            await self.db.set_index_job(
                job_id,
                {"status": "failed", "indexed": count, "error": str(exc)[:500], "finished_at": utc_now()},
            )
            await self.bot.send_message(
                admin_chat_id,
                f"Historical indexing stopped after {count} files: {str(exc)[:300]}",
            )
        finally:
            self.running_chats.discard(normalized_chat_id)

