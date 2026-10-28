import os
import logging
import asyncio
from aiohttp import web
from telegram.ext import Application
from bot.database import init_db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def health_check(request):
    return web.Response(text="Dahiya-Shab Bot is active and running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server started and listening on port {port}")

async def main():
    logger.info("Initializing Database...")
    await init_db()
    logger.info("Database initialized successfully.")

    bot_token = os.environ.get("BOT_TOKEN") or os.environ.get("TOKEN")
    if not bot_token:
        raise ValueError("Error: BOT_TOKEN environment variable is missing!")

    application = Application.builder().token(bot_token).build()

    # Handlers Registration
    try:
        from bot.handlers import setup_handlers, register_handlers
        try:
            setup_handlers(application)
        except Exception:
            register_handlers(application)
        logger.info("Handlers loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load handlers: {e}")

    await start_web_server()

    logger.info("Starting bot polling...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    stop_event = asyncio.Event()
    await stop_event.wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
