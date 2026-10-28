import motor.motor_asyncio
import os
from telegram import Bot
import logging

logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://...")
DB_NAME = os.getenv("DB_NAME", "dahiya_movie_store")

client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]

async def init_db():
    try:
        await db.command("ping")
        logger.info("MongoDB connection established and indexes are ready.")
    except Exception as e:
        logger.error(f"MongoDB connection failed: {e}")
