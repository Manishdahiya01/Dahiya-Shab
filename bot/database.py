import motor.motor_asyncio
import os
import logging

logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI or MONGO_URI.strip() == "":
    logger.error("CRITICAL: MONGO_URI environment variable is missing or empty in Render settings!")

client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI if MONGO_URI else "mongodb://localhost:27017")
db = client.get_database("dahiya_movie_store")

async def init_db():
    try:
        await db.command("ping")
        logger.info("MongoDB connection established successfully.")
    except Exception as e:
        logger.error(f"MongoDB connection failed: {e}")
