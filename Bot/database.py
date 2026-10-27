from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from pymongo import AsyncMongoClient, ASCENDING, DESCENDING, TEXT, UpdateOne
from pymongo.errors import OperationFailure

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: dict[str, Any] = {
    "_id": "main",
    "bot_enabled": True,
    "bot_description": "Search Dahiya Movie Store",
    "welcome_photo_file_id": None,
    "auto_delete": {"enabled": False, "seconds": 300},
    "subscription": {"enabled": False},
    "new_indexing_enabled": True,
    "not_found_message": "Sorry, this movie was not found. Please check the name and try again.",
    "broadcast": {},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def movie_key(source_chat_id: int | str, source_message_id: int) -> str:
    raw = f"{source_chat_id}:{source_message_id}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]


class Database:
    """MongoDB-backed state. No deployment step drops or recreates user data."""

    def __init__(self, uri: str, database_name: str):
        self.client = AsyncMongoClient(
            uri,
            serverSelectionTimeoutMS=15000,
            connectTimeoutMS=15000,
            retryWrites=True,
        )
        self.db = self.client[database_name]
        self.settings = self.db.settings
        self.members = self.db.members
        self.requests = self.db.requests
        self.sources = self.db.sources
        self.subscriptions = self.db.subscription_channels
        self.movies = self.db.movie_index
        self.deliveries = self.db.pending_deletions
        self.index_jobs = self.db.index_jobs

    async def connect(self) -> None:
        await self.client.admin.command("ping")
        defaults = {key: value for key, value in DEFAULT_SETTINGS.items() if key != "_id"}
        await self.settings.update_one(
            {"_id": "main"}, {"$setOnInsert": defaults}, upsert=True
        )
        await self.members.create_index("user_id", unique=True)
        await self.members.create_index([("last_seen", DESCENDING)])
        await self.requests.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        await self.sources.create_index("chat_id", unique=True)
        await self.subscriptions.create_index("chat_id", unique=True)
        await self.movies.create_index("file_key", unique=True)
        await self.movies.create_index(
            [("title", TEXT), ("normalized_title", TEXT), ("caption", TEXT)],
            name="movie_search_text",
        )
        await self.movies.create_index([("source_chat_id", ASCENDING), ("source_message_id", ASCENDING)], unique=True)
        await self.movies.create_index([("normalized_title", ASCENDING), ("year", ASCENDING)])
        await self.deliveries.create_index("expires_at")
        await self.index_jobs.create_index([("chat_id", ASCENDING), ("started_at", DESCENDING)])
        logger.info("MongoDB connection established and indexes are ready.")

    async def close(self) -> None:
        await self.client.close()

    async def get_settings(self) -> dict[str, Any]:
        current = await self.settings.find_one({"_id": "main"})
        if not current:
            defaults = {key: value for key, value in DEFAULT_SETTINGS.items() if key != "_id"}
            await self.settings.update_one(
                {"_id": "main"}, {"$setOnInsert": defaults}, upsert=True
            )
            current = await self.settings.find_one({"_id": "main"})
        return current or DEFAULT_SETTINGS.copy()

    async def update_settings(self, values: dict[str, Any]) -> None:
        await self.settings.update_one({"_id": "main"}, {"$set": values}, upsert=True)

    async def register_member(self, user: Any) -> None:
        if not user:
            return
        now = utc_now()
        await self.members.update_one(
            {"user_id": user.id},
            {
                "$set": {
                    "username": user.username,
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "last_seen": now,
                    "is_active": True,
                },
                "$setOnInsert": {"joined_at": now, "request_count": 0},
            },
            upsert=True,
        )

    async def record_request(self, user: Any, text: str) -> None:
        now = utc_now()
        await self.requests.insert_one(
            {
                "user_id": user.id,
                "username": user.username,
                "name": " ".join(filter(None, [user.first_name, user.last_name])),
                "query": text[:200],
                "created_at": now,
            }
        )
        await self.members.update_one(
            {"user_id": user.id},
            {"$set": {"last_request": text[:200], "last_request_at": now}, "$inc": {"request_count": 1}},
            upsert=True,
        )

    async def save_source(self, chat_id: int, title: str, source_type: str = "channel", username: str | None = None) -> None:
        await self.sources.update_one(
            {"chat_id": str(chat_id)},
            {"$set": {"title": title[:160], "type": source_type, "username": username, "updated_at": utc_now()}},
            upsert=True,
        )

    async def remove_source(self, chat_id: int | str) -> bool:
        result = await self.sources.delete_one({"chat_id": str(chat_id)})
        return result.deleted_count > 0

    async def rename_source(self, chat_id: int | str, title: str) -> bool:
        result = await self.sources.update_one(
            {"chat_id": str(chat_id)},
            {"$set": {"title": title[:160], "updated_at": utc_now()}},
        )
        return result.matched_count > 0

    async def get_sources(self) -> list[dict[str, Any]]:
        return await self.sources.find().sort("title", ASCENDING).to_list(length=500)

    async def save_subscription(self, chat_id: str, title: str, join_url: str) -> None:
        await self.subscriptions.update_one(
            {"chat_id": chat_id},
            {"$set": {"title": title[:160], "join_url": join_url.strip(), "updated_at": utc_now()}},
            upsert=True,
        )

    async def get_subscriptions(self) -> list[dict[str, Any]]:
        return await self.subscriptions.find().sort("title", ASCENDING).to_list(length=100)

    async def remove_subscription(self, chat_id: str) -> bool:
        result = await self.subscriptions.delete_one({"chat_id": chat_id})
        return result.deleted_count > 0

    async def save_movie(self, document: dict[str, Any]) -> None:
        key = movie_key(document["source_chat_id"], int(document["source_message_id"]))
        document["file_key"] = key
        document["indexed_at"] = utc_now()
        await self.movies.update_one(
            {
                "source_chat_id": str(document["source_chat_id"]),
                "source_message_id": int(document["source_message_id"]),
            },
            {"$set": document, "$setOnInsert": {"created_at": utc_now()}},
            upsert=True,
        )

    async def save_movies_bulk(self, documents: list[dict[str, Any]]) -> int:
        if not documents:
            return 0
        operations = []
        for doc in documents:
            doc["source_chat_id"] = str(doc["source_chat_id"])
            doc["source_message_id"] = int(doc["source_message_id"])
            doc["file_key"] = movie_key(doc["source_chat_id"], doc["source_message_id"])
            doc["indexed_at"] = utc_now()
            operations.append(
                UpdateOne(
                    {"source_chat_id": doc["source_chat_id"], "source_message_id": doc["source_message_id"]},
                    {"$set": doc, "$setOnInsert": {"created_at": utc_now()}},
                    upsert=True,
                )
            )
        await self.movies.bulk_write(operations, ordered=False)
        return len(operations)

    async def search_movies(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        query = query.strip()[:120]
        if not query:
            return []
        try:
            cursor = self.movies.find(
                {"$text": {"$search": query}},
                {"score": {"$meta": "textScore"}},
            ).sort([("score", {"$meta": "textScore"})]).limit(limit)
            results = await cursor.to_list(length=limit)
            if results:
                return results
        except OperationFailure:
            logger.exception("Text search failed; trying escaped regular-expression search.")

        escaped = re_escape(query)
        cursor = self.movies.find(
            {
                "$or": [
                    {"title": {"$regex": escaped, "$options": "i"}},
                    {"normalized_title": {"$regex": escaped, "$options": "i"}},
                    {"caption": {"$regex": escaped, "$options": "i"}},
                ]
            }
        ).sort([("year", DESCENDING), ("title", ASCENDING)]).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_movie(self, file_key: str) -> dict[str, Any] | None:
        return await self.movies.find_one({"file_key": file_key})

    async def all_members(self, offset: int = 0, limit: int = 15) -> list[dict[str, Any]]:
        return await self.members.find().sort("last_seen", DESCENDING).skip(offset).limit(limit).to_list(length=limit)

    async def count_members(self) -> int:
        return await self.members.count_documents({})

    async def request_stats(self) -> dict[str, Any]:
        total = await self.requests.count_documents({})
        latest = await self.requests.find().sort("created_at", DESCENDING).limit(8).to_list(length=8)
        indexed = await self.movies.count_documents({})
        sources = await self.sources.count_documents({})
        return {"requests": total, "latest": latest, "indexed": indexed, "sources": sources}

    async def make_delivery(
        self,
        chat_id: int,
        message_id: int,
        status_message_id: int,
        expires_at: Any,
    ) -> None:
        await self.deliveries.update_one(
            {"chat_id": chat_id, "message_id": message_id},
            {
                "$set": {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "status_message_id": status_message_id,
                    "expires_at": expires_at,
                    "last_countdown_edit": None,
                }
            },
            upsert=True,
        )

    async def due_deliveries(self, now: datetime) -> list[dict[str, Any]]:
        return await self.deliveries.find({"expires_at": {"$lte": now}}).limit(100).to_list(length=100)

    async def pending_deliveries(self, now: datetime, before: datetime) -> list[dict[str, Any]]:
        return await self.deliveries.find(
            {
                "expires_at": {"$gt": now},
                "$or": [
                    {"last_countdown_edit": None},
                    {"last_countdown_edit": {"$lte": before}},
                    {"last_countdown_edit": {"$exists": False}},
                ],
            }
        ).sort("last_countdown_edit", ASCENDING).limit(50).to_list(length=50)

    async def update_delivery(self, chat_id: int, message_id: int, values: dict[str, Any]) -> None:
        await self.deliveries.update_one({"chat_id": chat_id, "message_id": message_id}, {"$set": values})

    async def remove_delivery(self, chat_id: int, message_id: int) -> None:
        await self.deliveries.delete_one({"chat_id": chat_id, "message_id": message_id})

    async def set_index_job(self, job_id: str, values: dict[str, Any]) -> None:
        await self.index_jobs.update_one({"_id": job_id}, {"$set": values}, upsert=True)

    async def get_latest_index_job(self, chat_id: int | str) -> dict[str, Any] | None:
        return await self.index_jobs.find_one({"chat_id": str(chat_id)}, sort=[("started_at", DESCENDING)])


def re_escape(value: str) -> str:
    import re

    return re.escape(value)

