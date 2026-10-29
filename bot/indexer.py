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
        self.pause_requested_chats: set[str] = set()

    def request_pause(self, chat_id: int | str) -> bool:
        normalized_chat_id = str(chat_id)
        if normalized_chat_id not in self.running_chats:
            return False
        self.pause_requested_chats.add(normalized_chat_id)
        return True

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
        return bool(await self.db.save_movie(doc))

    async def scan_chat(
        self,
        chat_id: int | str,
        admin_chat_id: int,
        *,
        resume: bool = False,
    ) -> None:
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
            "filter_cursors": {"documents": 0, "videos": 0},
            "completed_filters": [],
            "current_filter": "documents",
            "error": None,
        }
        progress_message_id: int | None = None
        source: dict[str, Any] = {"title": normalized_chat_id}
        try:
            source = await self.db.sources.find_one({"chat_id": normalized_chat_id})
            if not source:
                raise RuntimeError("This source is not registered in Index groups.")
            previous_job = await self.db.get_latest_index_job(normalized_chat_id)
            resumable_statuses = {"running", "failed", "paused"}
            can_resume = bool(
                previous_job and previous_job.get("status") in resumable_statuses
            )
            if resume and not can_resume:
                await self.bot.send_message(
                    admin_chat_id,
                    "There is no unfinished scan to resume. Start an old-file scan instead.",
                )
                return
            if can_resume and previous_job:
                job_id = str(previous_job.get("_id") or job_id)
                job = {
                    key: value
                    for key, value in previous_job.items()
                    if key != "_id"
                }
                job["resumed_at"] = utc_now()
                job["resume_count"] = int(job.get("resume_count", 0) or 0) + 1
                job["status"] = "running"
                job["error"] = None

            entity = await self.client.get_entity(int(normalized_chat_id))
            media_filters = [
                ("documents", InputMessagesFilterDocument()),
                ("videos", InputMessagesFilterVideo()),
            ]
            media_totals = []
            for _, media_filter in media_filters:
                media_count = await self.client.get_messages(
                    entity,
                    limit=0,
                    filter=media_filter,
                )
                media_totals.append(
                    max(0, int(getattr(media_count, "total", 0) or 0))
                )
            job["files_total"] = sum(media_totals)

            if can_resume:
                cursors = job.get("filter_cursors")
                completed_filters = job.get("completed_filters")
                if not isinstance(cursors, dict):
                    # Older job records only kept one cursor. Infer which filter
                    # was active from the completed document count.
                    legacy_cursor = max(0, int(job.get("last_message_id", 0) or 0))
                    processed = max(0, int(job.get("files_processed", 0) or 0))
                    if processed < media_totals[0]:
                        cursors = {"documents": legacy_cursor, "videos": 0}
                        completed_filters = []
                    else:
                        cursors = {
                            "documents": legacy_cursor,
                            "videos": legacy_cursor if processed > media_totals[0] else 0,
                        }
                        completed_filters = ["documents"]
                else:
                    cursors = {
                        "documents": max(0, int(cursors.get("documents", 0) or 0)),
                        "videos": max(0, int(cursors.get("videos", 0) or 0)),
                    }
                    completed_filters = list(completed_filters or [])
                job["filter_cursors"] = cursors
                job["completed_filters"] = completed_filters
                job["files_processed"] = max(0, int(job.get("files_processed", 0) or 0))
                job["indexed"] = max(0, int(job.get("indexed", 0) or 0))
                job["skipped"] = max(0, int(job.get("skipped", 0) or 0))
                job["errors"] = max(0, int(job.get("errors", 0) or 0))

            job["updated_at"] = utc_now()
            await self.db.set_index_job(job_id, job)
            try:
                progress_message = await self.bot.send_message(
                    admin_chat_id,
                    _index_progress_text(source.get("title", normalized_chat_id), job),
                )
                progress_message_id = progress_message.message_id
            except Exception:
                logger.exception("Could not send historical-index progress message.")

            async def pause_if_requested() -> bool:
                if normalized_chat_id not in self.pause_requested_chats:
                    return False
                job.update(
                    {
                        "status": "paused",
                        "paused_at": utc_now(),
                        "updated_at": utc_now(),
                        "error": None,
                    }
                )
                await self.db.set_index_job(job_id, job)
                if progress_message_id:
                    try:
                        await self.bot.edit_message_text(
                            chat_id=admin_chat_id,
                            message_id=progress_message_id,
                            text=_index_progress_text(source.get("title", normalized_chat_id), job),
                        )
                    except Exception:
                        logger.debug("Could not publish paused indexing status.", exc_info=True)
                return True

            last_update = time.monotonic()
            for filter_name, media_filter in media_filters:
                if await pause_if_requested():
                    return
                if filter_name in job["completed_filters"]:
                    continue
                job["current_filter"] = filter_name
                await self.db.set_index_job(job_id, job)
                if await pause_if_requested():
                    return
                cursor = int(job["filter_cursors"].get(filter_name, 0) or 0)
                async for message in self.client.iter_messages(
                    entity,
                    reverse=True,
                    filter=media_filter,
                    min_id=cursor,
                ):
                    message_id = int(message.id)
                    indexed = await self.index_telethon_message(
                        message,
                        normalized_chat_id,
                        source,
                    )
                    job["files_processed"] += 1
                    job["last_message_id"] = message_id
                    job["filter_cursors"][filter_name] = message_id
                    if indexed:
                        job["indexed"] += 1
                    else:
                        job["skipped"] += 1
                    job["updated_at"] = utc_now()
                    # Persist after every item. A deploy can then resume from
                    # the last completed message instead of rescanning history.
                    await self.db.set_index_job(job_id, job)

                    if job["files_processed"] % 25 == 0 or time.monotonic() - last_update >= 5:
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
                    if await pause_if_requested():
                        return

                if await pause_if_requested():
                    return
                if filter_name not in job["completed_filters"]:
                    job["completed_filters"].append(filter_name)
                job["updated_at"] = utc_now()
                await self.db.set_index_job(job_id, job)

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
            job["errors"] = int(job.get("errors", 0) or 0) + 1
            job["error"] = str(exc)[:500]
            job["finished_at"] = utc_now()
            job["updated_at"] = utc_now()
            try:
                await self.db.set_index_job(job_id, job)
            except Exception:
                logger.exception("Could not persist the failed indexing checkpoint.")
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
            self.pause_requested_chats.discard(normalized_chat_id)

