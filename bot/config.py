from __future__ import annotations

import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is missing.")
    return value


@dataclass(frozen=True)
class Config:
    bot_token: str
    mongo_uri: str
    mongo_database: str
    admin_user_ids: frozenset[int]
    telegram_api_id: int | None
    telegram_api_hash: str | None
    telethon_session: str | None
    health_host: str
    port: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        raw_admins = _required("ADMIN_USER_IDS")
        try:
            admin_ids = frozenset(
                int(value.strip())
                for value in raw_admins.split(",")
                if value.strip()
            )
        except ValueError as exc:
            raise RuntimeError("ADMIN_USER_IDS must be a comma-separated list of numeric Telegram user IDs.") from exc
        if not admin_ids:
            raise RuntimeError("ADMIN_USER_IDS must contain at least one Telegram user ID.")

        raw_api_id = os.getenv("TELEGRAM_API_ID", "").strip()
        try:
            api_id = int(raw_api_id) if raw_api_id else None
        except ValueError as exc:
            raise RuntimeError("TELEGRAM_API_ID must be numeric.") from exc

        raw_port = os.getenv("PORT", "10000").strip()
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise RuntimeError("PORT must be numeric.") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError("PORT must be between 1 and 65535.")

        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip() or None
        session = os.getenv("TELETHON_SESSION", "").strip() or None
        indexing_parts = (api_id, api_hash, session)
        if any(indexing_parts) and not all(indexing_parts):
            raise RuntimeError(
                "Historical indexing needs TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELETHON_SESSION together."
            )

        return cls(
            bot_token=_required("BOT_TOKEN"),
            mongo_uri=_required("MONGODB_URI"),
            mongo_database=os.getenv("MONGODB_DATABASE", "dahiya_movie_store").strip(),
            admin_user_ids=admin_ids,
            telegram_api_id=api_id,
            telegram_api_hash=api_hash,
            telethon_session=session,
            health_host=os.getenv("HEALTH_HOST", "0.0.0.0").strip(),
            port=port,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper().strip(),
        )

    @property
    def historical_indexing_enabled(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash and self.telethon_session)

