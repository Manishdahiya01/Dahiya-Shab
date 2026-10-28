import os
import logging
import asyncio
from aiohttp import web
from telegram.ext import Application
from bot.database import init_db

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def health_check(request):
    """Render port requirement ko satisfy karne ke liye health check endpoint"""
    return web.Response(text="Dahiya-Shab Bot is active and running!")

async def start_web_server():
    """Aiohttp web server jo Render ke diye gaye PORT par listen karega"""
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
    """Database, Web Server aur Telegram Bot ko ek sath run karne ka main function"""
    logger.info("Initializing Database...")
    await init_db()
    logger.info("Database initialized successfully.")

    # Environment variables se direct token uthana taaki import error na ho
    bot_token = os.environ.get("BOT_TOKEN") or os.environ.get("TOKEN")
    if not bot_token:
        raise ValueError("Error: BOT_TOKEN environment variable is missing!")

    # Telegram Bot Application setup
    application = Application.builder().token(bot_token).build()

    # Aiohttp Web Server start karna taaki port open rahe
    await start_web_server()

    # Bot polling start karna
    logger.info("Starting bot polling...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # Infinite loop taaki application band na ho
    stop_event = asyncio.Event()
    await stop_event.wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
