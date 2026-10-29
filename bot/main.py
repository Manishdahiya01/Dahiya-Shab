from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone
from typing import Any

from aiohttp import web
from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application

from .config import Config
from .admins import AdminRegistry
from .database import Database
from .handlers import register_handlers
from .indexer import HistoricalIndexer


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def deletion_worker(db: Database, bot: Any, shutdown: asyncio.Event) -> None:
    logger = logging.getLogger(__name__)
    while not shutdown.is_set():
        now = datetime.now(timezone.utc)
        try:
            due = await db.due_deliveries(now)
            for delivery in due:
                chat_id = delivery["chat_id"]
                message_id = delivery["message_id"]
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except BadRequest as exc:
                    if "message to delete not found" not in str(exc).lower():
                        logger.warning("Could not delete timed message %s: %s", message_id, exc)
                except TelegramError:
                    logger.exception("Auto-delete failed for message %s.", message_id)
                    continue
                status_id = delivery.get("status_message_id")
                if status_id:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=status_id)
                    except TelegramError:
                        logger.debug("Countdown message %s was already unavailable.", status_id)
                await db.remove_delivery(chat_id, message_id)

            before = now - timedelta(seconds=1)
            pending = await db.pending_deliveries(now, before)
            updated_chats: set[int] = set()
            for delivery in pending:
                if delivery["chat_id"] in updated_chats:
                    continue
                updated_chats.add(delivery["chat_id"])
                expiry = delivery["expires_at"]
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                remaining = max(0, int((expiry - now).total_seconds() + 0.999))
                status_id = delivery.get("status_message_id")
                if status_id:
                    try:
                        await bot.edit_message_text(
                            chat_id=delivery["chat_id"],
                            message_id=status_id,
                            text=f"Auto-delete in {remaining} seconds.",
                        )
                    except BadRequest as exc:
                        if "message is not modified" not in str(exc).lower():
                            logger.debug("Countdown update was skipped: %s", exc)
                    except TelegramError:
                        logger.exception("Could not refresh countdown for message %s.", delivery["message_id"])
                await db.update_delivery(
                    delivery["chat_id"],
                    delivery["message_id"],
                    {"last_countdown_edit": now},
                )
        except Exception:
            logger.exception("Auto-delete worker cycle failed.")
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=1.0)
        except TimeoutError:
            pass


async def start_health_server(config: Config, db: Database) -> web.AppRunner:
    async def health(_: web.Request) -> web.Response:
        try:
            await db.client.admin.command("ping")
            return web.json_response({"status": "ok", "service": "dahiya-movie-store-bot"})
        except Exception:
            logging.getLogger(__name__).exception("Health check could not reach MongoDB.")
            return web.json_response({"status": "degraded", "database": "unavailable"}, status=503)

    async def root(_: web.Request) -> web.Response:
        return web.json_response(
            {"service": "Dahiya Movie Store Bot", "health": "/health"},
            headers={"Cache-Control": "no-store"},
        )

    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.health_host, config.port)
    await site.start()
    return runner


async def run() -> None:
    config = Config.from_env()
    configure_logging(config.log_level)
    logger = logging.getLogger(__name__)

    db = Database(config.mongo_uri, config.mongo_database)
    await db.connect()
    admins = AdminRegistry(
        config.admin_user_ids,
        await db.get_temporary_admins(),
    )

    application = (
        Application.builder()
        .token(config.bot_token)
        .connect_timeout(20)
        .read_timeout(30)
        .write_timeout(30)
        .get_updates_connect_timeout(20)
        .get_updates_read_timeout(35)
        .concurrent_updates(False)
        .build()
    )
    indexer = HistoricalIndexer(config, db, application.bot)
    application.bot_data["services"] = {
        "db": db,
        "indexer": indexer,
        "admins": admins,
    }
    register_handlers(application)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown.set)
        except NotImplementedError:
            pass

    health_runner: web.AppRunner | None = None
    delete_task: asyncio.Task[None] | None = None
    initialized = False
    try:
        await application.initialize()
        initialized = True
        await indexer.start()
        health_runner = await start_health_server(config, db)
        await application.start()
        if application.updater is None:
            raise RuntimeError("Telegram polling updater was not initialized.")
        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
            poll_interval=0.5,
            timeout=25,
        )
        delete_task = asyncio.create_task(deletion_worker(db, application.bot, shutdown))
        logger.info("Telegram bot is running; HTTP health endpoint is ready on port %s.", config.port)
        await shutdown.wait()
    finally:
        shutdown.set()
        if delete_task:
            delete_task.cancel()
            try:
                await delete_task
            except asyncio.CancelledError:
                pass
        if application.updater and application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        await indexer.stop()
        if initialized:
            await application.shutdown()
        if health_runner:
            await health_runner.cleanup()
        await db.close()
        logger.info("Bot shutdown completed.")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

