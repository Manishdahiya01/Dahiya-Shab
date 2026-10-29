from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class AdminRegistry:
    """Configured owners plus expiring admin grants stored in MongoDB."""

    def __init__(
        self,
        owner_ids: Iterable[int],
        temporary_admins: Iterable[dict[str, Any]] = (),
    ) -> None:
        self.owner_ids = frozenset(int(user_id) for user_id in owner_ids)
        self._temporary_expires: dict[int, datetime] = {}
        for record in temporary_admins:
            try:
                user_id = int(record["user_id"])
                expires_at = _as_utc(record["expires_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if user_id not in self.owner_ids and expires_at > datetime.now(timezone.utc):
                self._temporary_expires[user_id] = expires_at

    def __contains__(self, user_id: object) -> bool:
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            return False
        if user_id in self.owner_ids:
            return True
        expires_at = self._temporary_expires.get(user_id)
        if expires_at is None:
            return False
        if expires_at <= datetime.now(timezone.utc):
            self._temporary_expires.pop(user_id, None)
            return False
        return True

    def is_owner(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.owner_ids

    def add_temporary_admin(self, user_id: int, expires_at: datetime) -> None:
        if user_id not in self.owner_ids:
            self._temporary_expires[int(user_id)] = _as_utc(expires_at)

    def remove_temporary_admin(self, user_id: int) -> None:
        self._temporary_expires.pop(int(user_id), None)
