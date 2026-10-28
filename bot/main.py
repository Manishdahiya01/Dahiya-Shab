import os
import logging
import asyncio
from aiohttp import web
from telegram.ext import Application
from bot.config import TOKEN
from bot.database import init_db

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def health_check(request):
    """Render ke port check ke liye simple web server endpoint"""
    return web.Response(text="Dahiya-Shab Bot is running live!")

async def start_web_server():
    """Aiohttp web server start karta hai jo Render ke PORT par listen karega"""
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
    """Bot, database aur web server ko ek sath run karta hai"""
    logger.info("Initializing Database...")
    await init_db()
    logger.info("Database and Bot initialized successfully.")

    # Telegram Bot Application setup
    application = Application.builder().token(TOKEN).build()

    # Yahan apne handlers add kar lena agar alag se hain, jaise:
    # from bot.handlers import setup_handlers
    # setup_handlers(application)

    # Aiohttp Web Server aur Telegram Polling ko ek sath background mein chalana
    await start_web_server()

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
