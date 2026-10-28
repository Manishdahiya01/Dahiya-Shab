import logging
import os
from telegram.ext import ApplicationBuilder
from bot.database import init_db
from bot.handlers import register_handlers

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def post_init(application):
    await init_db()
    logger.info("Database and Bot initialized successfully.")

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is missing!")
        return

    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Register all handlers
    register_handlers(application)
    
    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
