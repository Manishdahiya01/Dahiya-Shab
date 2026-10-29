from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
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
    "group_search_enabled": True,
    "fuzzy_search_enabled": True,
    "powered_by": "Dahiya Movie Store | Search Zone",
    "welcome_message": "Send a movie or series name to search the indexed files.",
    "welcome_button": None,
    "custom_reply_buttons": [],
    "not_found_message": "Sorry, this movie was not found. Please check the name and try again.",
    "not_found_button": None,
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
        self.bot_groups = self.db.bot_groups
        self.temporary_admins = self.db.temporary_admins

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
        await self.bot_groups.create_index("chat_id", unique=True)
        await self.temporary_admins.create_index("user_id", unique=True)
        await self.temporary_admins.create_index("expires_at", expireAfterSeconds=0)
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
        return {**DEFAULT_SETTINGS, **(current or {})}

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

    async def save_movie(self, document: dict[str, Any]) -> bool:
        key = movie_key(document["source_chat_id"], int(document["source_message_id"]))
        document["file_key"] = key
        now = utc_now()
        document["indexed_at"] = now
        result = await self.movies.update_one(
            {
                "source_chat_id": str(document["source_chat_id"]),
                "source_message_id": int(document["source_message_id"]),
            },
            {"$setOnInsert": {**document, "created_at": now}},
            upsert=True,
        )
        return result.upserted_id is not None

    async def save_movies_bulk(self, documents: list[dict[str, Any]]) -> int:
        if not documents:
            return 0
        operations = []
        for doc in documents:
            doc["source_chat_id"] = str(doc["source_chat_id"])
            doc["source_message_id"] = int(doc["source_message_id"])
            doc["file_key"] = movie_key(doc["source_chat_id"], doc["source_message_id"])
            now = utc_now()
            doc["indexed_at"] = now
            operations.append(
                UpdateOne(
                    {"source_chat_id": doc["source_chat_id"], "source_message_id": doc["source_message_id"]},
                    {"$setOnInsert": {**doc, "created_at": now}},
                    upsert=True,
                )
            )
        result = await self.movies.bulk_write(operations, ordered=False)
        return int(result.upserted_count)

    async def search_movies(
        self,
        query: str,
        limit: int = 100,
        fuzzy_enabled: bool = True,
    ) -> list[dict[str, Any]]:
        query = query.strip()[:120]
        if not query:
            return []
        results: list[dict[str, Any]] = []
        try:
            cursor = self.movies.find(
                {"$text": {"$search": query}},
                {"score": {"$meta": "textScore"}},
            ).sort([("score", {"$meta": "textScore"})]).limit(limit)
            results = await cursor.to_list(length=limit)
        except OperationFailure:
            logger.exception("Text search failed; trying escaped regular-expression search.")

        if not results:
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
            results = await cursor.to_list(length=limit)
        if results or not fuzzy_enabled:
            return results

        normalized_query = _normalize_search(query)
        query_tokens = normalized_query.split()
        if not query_tokens or not any(len(token) >= 3 for token in query_tokens):
            return []

        candidates = await self.movies.find().sort("indexed_at", DESCENDING).limit(5000).to_list(length=5000)
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in candidates:
            title = _normalize_search(item.get("normalized_title") or item.get("title") or "")
            title_tokens = title.split()
            if not title_tokens:
                continue
            token_scores = [
                max(SequenceMatcher(None, token, candidate).ratio() for candidate in title_tokens)
                for token in query_tokens
            ]
            coverage = sum(score >= 0.58 for score in token_scores) / len(token_scores)
            token_score = sum(token_scores) / len(token_scores)
            whole_score = SequenceMatcher(None, normalized_query, title).ratio()
            score = max(whole_score, token_score * 0.8 + coverage * 0.2)
            if score >= 0.66:
                scored.append((score, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    async def get_movie(self, file_key: str) -> dict[str, Any] | None:
        return await self.movies.find_one({"file_key": file_key})

    async def duplicate_groups(self, limit: int = 20) -> list[dict[str, Any]]:
        pipeline = [
            {"$match": {"file_unique_id": {"$type": "string", "$ne": ""}}},
            {"$sort": {"indexed_at": ASCENDING}},
            {
                "$group": {
                    "_id": "$file_unique_id",
                    "count": {"$sum": 1},
                    "files": {
                        "$push": {
                            "file_key": "$file_key",
                            "filename": "$filename",
                            "title": "$title",
                            "source_title": "$source_title",
                        }
                    },
                }
            },
            {"$match": {"count": {"$gt": 1}}},
            {"$sort": {"count": DESCENDING}},
            {"$limit": max(1, min(limit, 100))},
        ]
        return await self.movies.aggregate(pipeline).to_list(length=max(1, min(limit, 100)))

    async def delete_movie_index(self, file_key: str) -> bool:
        result = await self.movies.delete_one({"file_key": file_key})
        return result.deleted_count > 0

    async def all_members(self, offset: int = 0, limit: int = 15) -> list[dict[str, Any]]:
        return await self.members.find().sort("last_seen", DESCENDING).skip(offset).limit(limit).to_list(length=limit)

    async def count_members(self) -> int:
        return await self.members.count_documents({})

    async def active_member_count(self, since: datetime) -> int:
        return await self.members.count_documents({"last_seen": {"$gte": since}})

    async def active_members(self, since: datetime, limit: int = 10) -> list[dict[str, Any]]:
        return await self.members.find({"last_seen": {"$gte": since}}).sort("last_seen", DESCENDING).limit(limit).to_list(length=limit)

    async def recent_requests(self, offset: int = 0, limit: int = 10) -> list[dict[str, Any]]:
        return await self.requests.find().sort("created_at", DESCENDING).skip(offset).limit(limit).to_list(length=limit)

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

    async def get_delivery(self, chat_id: int, message_id: int) -> dict[str, Any] | None:
        return await self.deliveries.find_one({"chat_id": chat_id, "message_id": message_id})

    async def expired_delivery_count(self, now: datetime) -> int:
        return await self.deliveries.count_documents({"expires_at": {"$lte": now}})

    async def purge_expired_deliveries(self, now: datetime) -> int:
        result = await self.deliveries.delete_many({"expires_at": {"$lte": now}})
        return int(result.deleted_count)

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

    async def index_data_counts(self) -> dict[str, int]:
        return {
            "files": int(await self.movies.count_documents({})),
            "checkpoints": int(await self.index_jobs.count_documents({})),
        }

    async def clear_index_data(self) -> dict[str, int]:
        files = await self.movies.delete_many({})
        checkpoints = await self.index_jobs.delete_many({})
        return {
            "files": int(files.deleted_count),
            "checkpoints": int(checkpoints.deleted_count),
        }

    async def get_temporary_admins(self) -> list[dict[str, Any]]:
        return await self.temporary_admins.find(
            {"expires_at": {"$gt": utc_now()}}
        ).sort("expires_at", ASCENDING).to_list(length=None)

    async def list_temporary_admins(self, offset: int = 0, limit: int = 10) -> list[dict[str, Any]]:
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(int(limit), 50))
        return await (
            self.temporary_admins.find({"expires_at": {"$gt": utc_now()}})
            .sort("expires_at", ASCENDING)
            .skip(safe_offset)
            .limit(safe_limit)
            .to_list(length=safe_limit)
        )

    async def count_temporary_admins(self) -> int:
        return await self.temporary_admins.count_documents({"expires_at": {"$gt": utc_now()}})

    async def grant_temporary_admin(
        self,
        user_id: int,
        granted_by_id: int,
        expires_at: datetime,
    ) -> None:
        now = utc_now()
        await self.temporary_admins.update_one(
            {"user_id": int(user_id)},
            {
                "$set": {
                    "user_id": int(user_id),
                    "granted_by_id": int(granted_by_id),
                    "expires_at": expires_at,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def remove_temporary_admin(self, user_id: int) -> bool:
        result = await self.temporary_admins.delete_one({"user_id": int(user_id)})
        return result.deleted_count > 0

    async def save_bot_group(
        self,
        chat_id: int | str,
        title: str,
        chat_type: str,
        bot_status: str | None = None,
        added_by_id: int | None = None,
        added_by_name: str | None = None,
        added_by_username: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "chat_id": str(chat_id),
            "title": title[:160],
            "type": chat_type,
            "updated_at": utc_now(),
        }
        if bot_status is not None:
            values["bot_status"] = bot_status
        if added_by_id is not None:
            values["added_by_id"] = int(added_by_id)
        if added_by_name:
            values["added_by_name"] = added_by_name[:160]
        if added_by_username:
            values["added_by_username"] = added_by_username.lstrip("@")[:64]
        await self.bot_groups.update_one(
            {"chat_id": str(chat_id)},
            {"$set": values},
            upsert=True,
        )

    async def get_bot_group(self, chat_id: int | str) -> dict[str, Any] | None:
        return await self.bot_groups.find_one({"chat_id": str(chat_id)})

    async def get_bot_groups(self) -> list[dict[str, Any]]:
        return await self.bot_groups.find().sort("title", ASCENDING).to_list(length=500)

    async def count_bot_groups(self) -> int:
        return await self.bot_groups.count_documents({})

    async def remove_bot_group(self, chat_id: int | str) -> bool:
        result = await self.bot_groups.delete_one({"chat_id": str(chat_id)})
        return result.deleted_count > 0


def re_escape(value: str) -> str:
    import re

    return re.escape(value)


def _normalize_search(value: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )

