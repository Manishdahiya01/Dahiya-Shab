
Run locally with:
    python tools/create_telethon_session.py

The printed value is a credential. Put it directly into Render's TELETHON_SESSION
secret field. Do not commit it, send it in chat, or include it in a ZIP.
"""

from __future__ import annotations

import asyncio
import getpass
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def create_session() -> None:
    raw_api_id = os.getenv("TELEGRAM_API_ID", "").strip() or input("Telegram API ID: ").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip() or getpass.getpass("Telegram API hash: ").strip()
    try:
        api_id = int(raw_api_id)
    except ValueError as exc:
        raise SystemExit("Telegram API ID must be numeric.") from exc
    if not api_hash:
        raise SystemExit("Telegram API hash cannot be empty.")

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()
    session_string = client.session.save()
    print("\nCopy this into Render as TELETHON_SESSION. Treat it like a password:")
    print(session_string)
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(create_session())
