from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputProfilePhotoStatic, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .database import Database, utc_now
from .indexer import HistoricalIndexer
from .keyboards import (
    admin_home,
    filter_options_keyboard,
    join_gate,
    members_keyboard,
    results_keyboard,
    settings_menu,
    source_list,
    subscription_list,
    timer_menu,
)
from .metadata import parse_metadata

logger = logging.getLogger(__name__)
PAGE_SIZE = 10
MAX_SEND_ALL = 10


def _services(context: ContextTypes.DEFAULT_TYPE) -> tuple[Database, HistoricalIndexer, frozenset[int]]:
    services = context.application.bot_data["services"]
    return services["db"], services["indexer"], services["admins"]


def _is_admin(user_id: int | None, admins: frozenset[int]) -> bool:
    return user_id is not None and user_id in admins


async def _is_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database) -> bool:
    settings = await db.get_settings()
    if not settings.get("subscription", {}).get("enabled", False):
        return True
    channels = await db.get_subscriptions()
    if not channels:
        return True
    user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        return False
    for channel in channels:
        try:
            member = await context.bot.get_chat_member(channel["chat_id"], user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                return False
        except TelegramError:
            logger.exception("Could not verify subscription for channel %s.", channel.get("chat_id"))
            return False
    return True


async def _show_join_gate(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database) -> None:
    channels = await db.get_subscriptions()
    text = "Please join the required channel(s), then tap “I joined — check again”."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=join_gate(channels))
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=join_gate(channels))


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, admins = _services(context)
    user = update.effective_user
    await db.register_member(user)
    settings = await db.get_settings()
    if not settings.get("bot_enabled", True) and not _is_admin(user.id if user else None, admins):
        await update.effective_message.reply_text("The bot is temporarily paused. Please try again later.")
        return
    if not _is_admin(user.id if user else None, admins) and not await _is_subscribed(update, context, db):
        await _show_join_gate(update, context, db)
        return

    description = settings.get("bot_description") or "Search Dahiya Movie Store"
    text = (
        f"Welcome to Dahiya Movie Store.\n\n{description}\n\n"
        "Send a movie or series name to search the indexed files."
    )
    photo_id = settings.get("welcome_photo_file_id")
    if photo_id:
        try:
            await update.effective_message.reply_photo(photo_id, caption=text)
            return
        except TelegramError:
            logger.exception("The saved welcome photo could not be sent.")
    await update.effective_message.reply_text(text)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, admins = _services(context)
    user = update.effective_user
    if not _is_admin(user.id if user else None, admins):
        await update.effective_message.reply_text("You do not have permission to open the admin panel.")
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("Open the bot's private chat to use the admin panel.")
        return
    settings = await db.get_settings()
    await update.effective_message.reply_text("Dahiya Movie Store — Admin Panel", reply_markup=admin_home(settings))


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("admin_state", None)
    await update.effective_message.reply_text("Current action cancelled.")


async def _send_results(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database, page: int = 0) -> None:
    hits: list[dict[str, Any]] = context.user_data.get("search_hits", [])
    filters = context.user_data.get("search_filters", {})
    for key, selected in filters.items():
        if key == "languages":
            hits = [item for item in hits if selected in (item.get("languages") or [])]
        elif key == "year":
            hits = [item for item in hits if str(item.get("year")) == selected]
        elif key == "quality":
            hits = [item for item in hits if (item.get("quality") or "") == selected]
        elif key == "season":
            hits = [item for item in hits if str(item.get("season")) == selected]

    start = page * PAGE_SIZE
    page_items = hits[start : start + PAGE_SIZE]
    context.user_data["visible_file_keys"] = [item["file_key"] for item in page_items]
    filter_values = {
        "languages": sorted({lang for item in hits for lang in item.get("languages", [])}),
        "year": sorted({str(item["year"]) for item in hits if item.get("year") is not None}, reverse=True),
        "quality": sorted({item["quality"] for item in hits if item.get("quality")}),
        "season": sorted({str(item["season"]) for item in hits if item.get("season") is not None}),
    }
    context.user_data["filter_values"] = filter_values
    if not hits:
        settings = await db.get_settings()
        message = settings.get("not_found_message") or "Sorry, this movie was not found."
        if update.callback_query:
            await update.callback_query.edit_message_text(message)
        else:
            await update.effective_message.reply_text(message)
        return

    query = context.user_data.get("last_search", "")
    summary = f"Results for: {query}\nFound {len(hits)} indexed file(s). Select a file or filter:"
    keyboard = results_keyboard(page_items, page, len(hits), filter_values)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(summary, reply_markup=keyboard)
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                raise
    else:
        await update.effective_message.reply_text(summary, reply_markup=keyboard)


async def _run_search(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database, query: str) -> None:
    query = query.strip()[:120]
    if not query:
        await update.effective_message.reply_text("Send a movie or series name to search.")
        return
    await db.register_member(update.effective_user)
    await db.record_request(update.effective_user, query)
    hits = await db.search_movies(query)
    context.user_data["last_search"] = query
    context.user_data["search_hits"] = hits
    context.user_data["search_filters"] = {}
    await _send_results(update, context, db)


async def _broadcast(db: Database, context: ContextTypes.DEFAULT_TYPE, text: str, url: str | None) -> tuple[int, int]:
    sent = failed = 0
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Open link", url=url)]]) if url else None
    cursor = db.members.find({"is_active": True})
    async for member in cursor:
        try:
            await context.bot.send_message(
                chat_id=member["user_id"],
                text=text[:4000],
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            sent += 1
        except Forbidden:
            failed += 1
            await db.members.update_one({"user_id": member["user_id"]}, {"$set": {"is_active": False}})
        except TelegramError:
            failed += 1
            logger.exception("Broadcast delivery failed for member %s.", member.get("user_id"))
        await asyncio.sleep(0.04)
    return sent, failed


def _valid_url(value: str) -> str | None:
    match = re.search(r"https?://\S+", value, re.I)
    if not match:
        return None
    candidate = match.group(0).rstrip(".,)")
    parsed = urlparse(candidate)
    return candidate if parsed.scheme in ("http", "https") and parsed.netloc else None


async def _prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str, text: str) -> None:
    context.user_data["admin_state"] = state
    await update.callback_query.message.reply_text(text)


async def _handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    db, indexer, admins = _services(context)
    query = update.callback_query
    user = update.effective_user
    if not _is_admin(user.id if user else None, admins):
        await query.answer("Not allowed.", show_alert=True)
        return
    await query.answer()
    settings = await db.get_settings()

    if data == "adm:home":
        await query.edit_message_text("Dahiya Movie Store — Admin Panel", reply_markup=admin_home(settings))
    elif data == "adm:toggle_bot":
        enabled = not settings.get("bot_enabled", True)
        await db.update_settings({"bot_enabled": enabled})
        await query.edit_message_text(f"Bot is now {'ON' if enabled else 'OFF'}.", reply_markup=admin_home(await db.get_settings()))
    elif data == "adm:dashboard":
        stats = await db.request_stats()
        members = await db.count_members()
        recent = "\n".join(
            f"• {row.get('name') or row.get('username') or row['user_id']} ({row['user_id']}): {row.get('query', '')}"
            for row in stats["latest"]
        ) or "No requests yet."
        text = (
            "Live dashboard\n"
            f"Members: {members}\nRequests: {stats['requests']}\n"
            f"Indexed files: {stats['indexed']}\nIndex groups: {stats['sources']}\n\n"
            f"Recent requests:\n{recent}"
        )
        await query.edit_message_text(text[:3900], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="adm:home")]]))
    elif data.startswith("adm:members:"):
        offset = max(0, int(data.rsplit(":", 1)[1]))
        total = await db.count_members()
        rows = await db.all_members(offset, 15)
        if not rows:
            text = "No members are registered yet."
        else:
            lines = []
            for row in rows:
                display = " ".join(filter(None, [row.get("first_name"), row.get("last_name")])) or row.get("username") or "Member"
                lines.append(
                    f"{display} | ID: {row['user_id']}\n"
                    f"Requests: {row.get('request_count', 0)} | Latest: {row.get('last_request', '—')}"
                )
            text = f"Members {offset + 1}–{offset + len(rows)} of {total}\n\n" + "\n\n".join(lines)
        await query.edit_message_text(text[:3900], reply_markup=members_keyboard(offset, total))
    elif data == "adm:settings":
        await query.edit_message_text("Settings", reply_markup=settings_menu(settings))
    elif data == "adm:toggle_autodelete":
        enabled = not settings.get("auto_delete", {}).get("enabled", False)
        await db.update_settings({"auto_delete.enabled": enabled})
        await query.edit_message_text(
            f"Auto-delete is now {'ON' if enabled else 'OFF'}.",
            reply_markup=settings_menu(await db.get_settings()),
        )
    elif data == "adm:toggle_new_index":
        enabled = not settings.get("new_indexing_enabled", True)
        await db.update_settings({"new_indexing_enabled": enabled})
        await query.edit_message_text(
            f"Automatic indexing of new channel files is now {'ON' if enabled else 'OFF'}.",
            reply_markup=settings_menu(await db.get_settings()),
        )
    elif data == "adm:toggle_subscription":
        enabled = not settings.get("subscription", {}).get("enabled", False)
        await db.update_settings({"subscription.enabled": enabled})
        await query.edit_message_text(
            f"Force-subscription is now {'ON' if enabled else 'OFF'}.",
            reply_markup=subscription_list(await db.get_subscriptions(), enabled),
        )
    elif data == "adm:timer":
        seconds = settings.get("auto_delete", {}).get("seconds", 300)
        await query.edit_message_text("Choose an auto-delete duration:", reply_markup=timer_menu(seconds))
    elif data.startswith("adm:set_timer:"):
        seconds = int(data.rsplit(":", 1)[1])
        await db.update_settings({"auto_delete.seconds": seconds})
        await query.edit_message_text(
            f"Auto-delete duration set to {seconds} seconds.",
            reply_markup=settings_menu(await db.get_settings()),
        )
    elif data == "adm:custom_timer":
        await _prompt(update, context, "timer", "Send a custom duration in seconds (5–86400).")
    elif data == "adm:description":
        await _prompt(update, context, "description", "Send the new bot description (up to 512 characters).")
    elif data == "adm:photo":
        await _prompt(update, context, "photo", "Send a JPG/PNG photo. It will update the bot profile and the welcome image.")
    elif data == "adm:notfound":
        current = settings.get("not_found_message", "")
        await query.edit_message_text(
            f"Current movie-not-found message:\n\n{current}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Edit message", callback_data="adm:edit_notfound"),
                     InlineKeyboardButton("Reset", callback_data="adm:reset_notfound"),
                     InlineKeyboardButton("Clear", callback_data="adm:clear_notfound")],
                    [InlineKeyboardButton("Back", callback_data="adm:settings")],
                ]
            ),
        )
    elif data == "adm:edit_notfound":
        await _prompt(update, context, "notfound", "Send the replacement movie-not-found message.")
    elif data == "adm:reset_notfound":
        await db.update_settings({"not_found_message": "Sorry, this movie was not found. Please check the name and try again."})
        await query.edit_message_text("Movie-not-found message reset.", reply_markup=settings_menu(await db.get_settings()))
    elif data == "adm:clear_notfound":
        await db.update_settings({"not_found_message": ""})
        await query.edit_message_text("Custom message cleared. The built-in fallback will be used.", reply_markup=settings_menu(await db.get_settings()))
    elif data == "adm:sources":
        await query.edit_message_text("Index groups/channels. Removing a source does not erase indexed records.", reply_markup=source_list(await db.get_sources()))
    elif data == "adm:add_source":
        await _prompt(update, context, "add_source", "Send the source chat ID (for example -1001234567890). Add this bot as an administrator with permission to read channel posts.")
    elif data.startswith("adm:del_source:"):
        chat_id = data[len("adm:del_source:"):]
        removed = await db.remove_source(chat_id)
        await query.edit_message_text(
            "Source removed. Existing indexed records were kept." if removed else "Source was not found.",
            reply_markup=source_list(await db.get_sources()),
        )
    elif data.startswith("adm:edit_source:"):
        chat_id = data[len("adm:edit_source:"):]
        context.user_data["source_to_edit"] = chat_id
        await _prompt(update, context, "edit_source", "Send the new display name for this source group/channel.")
    elif data == "adm:old_index":
        sources = await db.get_sources()
        rows = [
            [InlineKeyboardButton(f"Scan {item.get('title') or item['chat_id']}"[:55], callback_data=f"adm:scan:{item['chat_id']}")]
            for item in sources[:20]
        ]
        rows.append([InlineKeyboardButton("Back", callback_data="adm:sources")])
        if not indexer.config.historical_indexing_enabled:
            text = "Historical indexing needs TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELETHON_SESSION in Render."
        elif indexer.error:
            text = f"Historical index session error: {indexer.error[:350]}"
        else:
            text = "Choose a registered source to scan its old files. Existing records will be updated, not cleared."
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
    elif data.startswith("adm:scan:"):
        chat_id = data[len("adm:scan:"):]
        await query.edit_message_text("Starting historical scan. The bot will send progress updates here.")
        asyncio.create_task(indexer.scan_chat(chat_id, query.message.chat_id))
    elif data == "adm:subscription":
        channels = await db.get_subscriptions()
        enabled = settings.get("subscription", {}).get("enabled", False)
        await query.edit_message_text("Force-subscription channels", reply_markup=subscription_list(channels, enabled))
    elif data == "adm:add_sub":
        await _prompt(
            update,
            context,
            "add_sub",
            "Send the channel chat ID and join link in this format:\n-1001234567890 | https://t.me/channel\nMake the bot an administrator in the channel so membership checks can work.",
        )
    elif data.startswith("adm:del_sub:"):
        chat_id = data[len("adm:del_sub:"):]
        removed = await db.remove_subscription(chat_id)
        await query.edit_message_text(
            "Subscription channel removed." if removed else "Channel was not found.",
            reply_markup=subscription_list(await db.get_subscriptions(), (await db.get_settings()).get("subscription", {}).get("enabled", False)),
        )
    elif data == "adm:broadcast":
        context.user_data["admin_state"] = "broadcast_text"
        await query.message.reply_text("Send the broadcast text. If it contains a link, the bot will add an Open link button.")
    elif data == "adm:broadcast_skip":
        pending = context.user_data.pop("broadcast_text", "")
        context.user_data.pop("admin_state", None)
        sent, failed = await _broadcast(db, context, pending, None)
        await query.message.reply_text(f"Broadcast complete. Sent: {sent}; failed: {failed}.")


async def _handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database) -> bool:
    state = context.user_data.get("admin_state")
    if not state:
        return False
    text = (update.effective_message.text or "").strip()
    if state == "description":
        if len(text) > 512:
            await update.effective_message.reply_text("Description is too long. Send no more than 512 characters.")
            return True
        await db.update_settings({"bot_description": text})
        try:
            await context.bot.set_my_description(description=text[:512])
            result = "The description was saved and updated on the bot profile."
        except TelegramError:
            logger.exception("Telegram rejected the updated bot description.")
            result = "The in-bot description was saved, but Telegram could not update the profile description. Check the BotFather configuration and try again."
        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(result, reply_markup=settings_menu(await db.get_settings()))
    elif state == "notfound":
        if not text or len(text) > 1000:
            await update.effective_message.reply_text("Send a message between 1 and 1000 characters.")
            return True
        await db.update_settings({"not_found_message": text})
        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text("Movie-not-found message updated.", reply_markup=settings_menu(await db.get_settings()))
    elif state == "timer":
        if not text.isdigit() or not 5 <= int(text) <= 86400:
            await update.effective_message.reply_text("Send a whole number of seconds from 5 to 86400.")
            return True
        await db.update_settings({"auto_delete.seconds": int(text)})
        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            f"Auto-delete duration set to {text} seconds.",
            reply_markup=settings_menu(await db.get_settings()),
        )
    elif state == "add_source":
        source_id = text.split()[0]
        try:
            numeric_id = int(source_id)
        except ValueError:
            await update.effective_message.reply_text("The source ID must be numeric, like -1001234567890.")
            return True
        try:
            chat = await context.bot.get_chat(numeric_id)
            await db.save_source(
                numeric_id,
                chat.title or chat.username or str(numeric_id),
                chat.type,
                chat.username,
            )
            context.user_data.pop("admin_state", None)
            await update.effective_message.reply_text(
                f"Added {chat.title or chat.username or numeric_id} to indexing sources. New posts will be indexed automatically when new indexing is ON.",
                reply_markup=source_list(await db.get_sources()),
            )
        except TelegramError:
            logger.exception("Could not add Telegram index source %s.", numeric_id)
            await update.effective_message.reply_text("Telegram could not access that chat. Add the bot there first, make it an administrator, then try again.")
    elif state == "edit_source":
        chat_id = context.user_data.pop("source_to_edit", "")
        if not text or len(text) > 160:
            await update.effective_message.reply_text("Send a name between 1 and 160 characters.")
            return True
        changed = await db.rename_source(chat_id, text)
        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            "Source name updated." if changed else "That source no longer exists.",
            reply_markup=source_list(await db.get_sources()),
        )
    elif state == "add_sub":
        parts = [part.strip() for part in text.split("|", 1)]
        if len(parts) != 2:
            await update.effective_message.reply_text("Use this format: -1001234567890 | https://t.me/channel")
            return True
        chat_id, join_url = parts
        parsed = urlparse(join_url)
        if not join_url or parsed.scheme not in ("http", "https") or not parsed.netloc:
            await update.effective_message.reply_text("The join link must start with https:// or http://.")
            return True
        try:
            chat = await context.bot.get_chat(chat_id)
            await db.save_subscription(str(chat.id), chat.title or chat.username or chat_id, join_url)
            context.user_data.pop("admin_state", None)
            await update.effective_message.reply_text("Required channel added.", reply_markup=subscription_list(await db.get_subscriptions(), (await db.get_settings()).get("subscription", {}).get("enabled", False)))
        except TelegramError:
            logger.exception("Could not add force-subscription channel %s.", chat_id)
            await update.effective_message.reply_text("Telegram could not access that channel. Add the bot there as an administrator and verify the chat ID.")
    elif state == "broadcast_text":
        if not text:
            await update.effective_message.reply_text("Broadcast text cannot be empty.")
            return True
        context.user_data["broadcast_text"] = text[:4000]
        detected_url = _valid_url(text)
        if detected_url:
            context.user_data.pop("admin_state", None)
            sent, failed = await _broadcast(db, context, text, detected_url)
            await update.effective_message.reply_text(f"Broadcast complete. Sent: {sent}; failed: {failed}.")
        else:
            context.user_data["admin_state"] = "broadcast_link"
            await update.effective_message.reply_text(
                "Send one optional link for the button, or tap Skip.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Skip", callback_data="adm:broadcast_skip")]]),
            )
    elif state == "broadcast_link":
        url = _valid_url(text)
        if not url:
            await update.effective_message.reply_text("Send a valid http/https link, or tap Skip.")
            return True
        pending = context.user_data.pop("broadcast_text", "")
        context.user_data.pop("admin_state", None)
        sent, failed = await _broadcast(db, context, pending, url)
        await update.effective_message.reply_text(f"Broadcast complete. Sent: {sent}; failed: {failed}.")
    else:
        context.user_data.pop("admin_state", None)
        return False
    return True


async def private_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, admins = _services(context)
    user = update.effective_user
    if not user:
        return
    await db.register_member(user)
    if _is_admin(user.id, admins) and await _handle_admin_text(update, context, db):
        return
    settings = await db.get_settings()
    if not settings.get("bot_enabled", True) and not _is_admin(user.id, admins):
        await update.effective_message.reply_text("The bot is temporarily paused. Please try again later.")
        return
    if not _is_admin(user.id, admins) and not await _is_subscribed(update, context, db):
        await _show_join_gate(update, context, db)
        return
    await _run_search(update, context, db, update.effective_message.text or "")


async def private_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, admins = _services(context)
    user = update.effective_user
    if not _is_admin(user.id if user else None, admins) or context.user_data.get("admin_state") != "photo":
        await update.effective_message.reply_text("Send a movie title to search. Admin photo changes are available from /admin.")
        return
    photo = update.effective_message.photo[-1]
    try:
        tg_file = await context.bot.get_file(photo.file_id)
        contents = bytes(await tg_file.download_as_bytearray())
        await context.bot.set_my_profile_photo(InputProfilePhotoStatic(photo=contents))
        await db.update_settings({"welcome_photo_file_id": photo.file_id})
        await update.effective_message.reply_text(
            "Bot profile picture and welcome image updated.",
            reply_markup=settings_menu(await db.get_settings()),
        )
    except TelegramError:
        logger.exception("Could not update bot profile photo.")
        # Keep a working in-bot welcome image even if Telegram rejects the profile-photo change.
        await db.update_settings({"welcome_photo_file_id": photo.file_id})
        await update.effective_message.reply_text(
            "The welcome image was saved, but Telegram did not accept the bot profile picture. Try a square JPG/PNG under 5 MB.",
            reply_markup=settings_menu(await db.get_settings()),
        )
    finally:
        context.user_data.pop("admin_state", None)


async def channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, _ = _services(context)
    message = update.channel_post
    await _save_new_source_message(message, db)


async def group_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, _ = _services(context)
    await _save_new_source_message(update.effective_message, db)


async def _save_new_source_message(message: Any, db: Database) -> None:
    if not message:
        return
    settings = await db.get_settings()
    if not settings.get("new_indexing_enabled", True):
        return
    source = await db.sources.find_one({"chat_id": str(message.chat_id)})
    if not source:
        return
    media = None
    for kind in ("document", "video", "audio", "animation", "photo", "voice", "video_note"):
        media = getattr(message, kind, None)
        if media:
            if kind == "photo":
                media = media[-1]
            media_type = kind
            break
    if not media:
        return
    caption = message.caption or ""
    filename = getattr(media, "file_name", "") or ""
    metadata = parse_metadata(caption, filename)
    await db.save_movie(
        {
            **metadata,
            "source_chat_id": str(message.chat_id),
            "source_message_id": int(message.message_id),
            "source_title": message.chat.title or message.chat.username or str(message.chat_id),
            "source_username": message.chat.username,
            "media_type": media_type,
            "bot_file_id": media.file_id,
            "file_unique_id": media.file_unique_id,
            "mime_type": getattr(media, "mime_type", "") or "",
            "size": getattr(media, "file_size", None),
        }
    )


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    data = query.data
    if data.startswith("adm:"):
        await _handle_admin_callback(update, context, data)
        return

    db, _, admins = _services(context)
    user = update.effective_user
    if data == "sub:check":
        if not await _is_subscribed(update, context, db):
            await query.answer("Join the required channel(s) first.", show_alert=True)
            return
        await query.answer("Subscription confirmed.")
        await query.message.reply_text("You are verified. Send a movie or series name to search.")
    elif data == "search:back":
        await query.answer()
        context.user_data.pop("search_hits", None)
        await query.message.reply_text("Send a movie or series name to search.")
    elif data == "search:results":
        await query.answer()
        await _send_results(update, context, db)
    elif data.startswith("filter:"):
        await query.answer()
        kind = data.split(":", 1)[1]
        values = context.user_data.get("filter_values", {}).get(kind, [])
        if not values:
            await query.message.reply_text("No filter options are available for this result.")
            return
        await query.message.reply_text(
            f"Choose {kind}:",
            reply_markup=filter_options_keyboard(kind, values),
        )
    elif data.startswith("choose:"):
        _, kind, raw_index = data.split(":", 2)
        values = context.user_data.get("filter_values", {}).get(kind, [])
        try:
            selected = values[int(raw_index)]
        except (IndexError, ValueError):
            await query.answer("That filter expired. Search again.", show_alert=True)
            return
        context.user_data.setdefault("search_filters", {})[kind] = selected
        await query.answer(f"Filter: {selected}")
        await _send_results(update, context, db)
    elif data.startswith("page:"):
        await query.answer()
        await _send_results(update, context, db, max(0, int(data.split(":", 1)[1])))
    elif data == "send_all":
        if not _is_admin(user.id if user else None, admins) and not await _is_subscribed(update, context, db):
            await _show_join_gate(update, context, db)
            return
        await query.answer()
        hits = context.user_data.get("search_hits", [])
        filters = context.user_data.get("search_filters", {})
        for key, selected in filters.items():
            if key == "languages":
                hits = [item for item in hits if selected in (item.get("languages") or [])]
            elif key == "year":
                hits = [item for item in hits if str(item.get("year")) == selected]
            elif key == "quality":
                hits = [item for item in hits if item.get("quality") == selected]
            elif key == "season":
                hits = [item for item in hits if str(item.get("season")) == selected]
        if not hits:
            await query.message.reply_text("There are no matching files to send.")
            return
        await query.message.reply_text(f"Sending up to {min(len(hits), MAX_SEND_ALL)} matching file(s).")
        for item in hits[:MAX_SEND_ALL]:
            await _deliver_movie(update, context, db, item)
            await asyncio.sleep(1.0)
    elif data.startswith("get:"):
        file_key = data[len("get:"):]
        if not _is_admin(user.id if user else None, admins) and not await _is_subscribed(update, context, db):
            await _show_join_gate(update, context, db)
            return
        await query.answer()
        item = await db.get_movie(file_key)
        if not item:
            await query.message.reply_text("This indexed file is no longer available.")
            return
        await _deliver_movie(update, context, db, item)
    else:
        await query.answer("This button is no longer available.", show_alert=True)


def _source_link(item: dict[str, Any]) -> str:
    username = item.get("source_username")
    if username:
        return f"https://t.me/{username}/{item['source_message_id']}"
    chat_id = str(item.get("source_chat_id", ""))
    internal_id = chat_id[4:] if chat_id.startswith("-100") else chat_id.lstrip("-")
    return f"https://t.me/c/{internal_id}/{item['source_message_id']}"


async def _deliver_movie(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database, item: dict[str, Any]) -> None:
    chat_id = update.effective_chat.id
    source_chat_id = item.get("source_chat_id")
    settings = await db.get_settings()
    auto_delete = settings.get("auto_delete", {})
    try:
        copied = await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=int(source_chat_id),
            message_id=int(item["source_message_id"]),
        )
        if auto_delete.get("enabled"):
            expires = utc_now() + timedelta(seconds=int(auto_delete.get("seconds", 300)))
            seconds = int(auto_delete.get("seconds", 300))
            status_message = await update.effective_message.reply_text(
                f"Auto-delete in {seconds} seconds."
            )
            await db.make_delivery(chat_id, copied.message_id, status_message.message_id, expires)
    except TelegramError:
        logger.exception("Bot API could not copy indexed message %s.", item.get("file_key"))
        url = _source_link(item)
        await update.effective_message.reply_text(
            "I could not copy this file. Open the original source message instead.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Open source file", url=url)]]),
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled bot update error.", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("Something went wrong. Please try again.")
        except TelegramError:
            logger.exception("Could not send the error reply.")


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, private_photo))
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, private_text))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, channel_post))
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, group_post))
    application.add_error_handler(error_handler)

