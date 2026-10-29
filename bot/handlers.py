from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputProfilePhotoStatic, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .admins import AdminRegistry
from .database import DEFAULT_SETTINGS, Database, movie_key, utc_now
from .indexer import HistoricalIndexer
from .keyboards import (
    admin_home,
    filter_options_keyboard,
    join_gate,
    members_keyboard,
    page_picker_keyboard,
    results_keyboard,
    settings_menu,
    source_list,
    subscription_list,
    subscription_remove_list,
    temporary_admin_remove_confirmation,
    temporary_admins_keyboard,
    timer_menu,
)
from .metadata import parse_metadata

logger = logging.getLogger(__name__)
PAGE_SIZE = 10
MAX_SEND_ALL = 10
TEMP_ADMIN_DURATIONS = {3600: "1 hour", 86400: "24 hours", 604800: "7 days"}


def _services(context: ContextTypes.DEFAULT_TYPE) -> tuple[Database, HistoricalIndexer, Any]:
    services = context.application.bot_data["services"]
    return services["db"], services["indexer"], services["admins"]


def _is_admin(user_id: int | None, admins: Any) -> bool:
    return user_id is not None and user_id in admins


def _search_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        return context.user_data
    callback = update.callback_query
    message = callback.message if callback else None
    if message:
        states = (getattr(context, "chat_data", {}) or {}).get("_search_results", {})
        return states.get(str(message.message_id), {})
    return {}


async def _search_command_text(
    text: str | None, context: ContextTypes.DEFAULT_TYPE
) -> str | None:
    if not text:
        return None
    match = re.fullmatch(
        r"\s*/(?:search|find)(?:@([A-Za-z0-9_]+))?(?:\s+([\s\S]*))?\s*",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    target_username, query = match.groups()
    if target_username and target_username.casefold() != (await _bot_username(context)).casefold():
        return None
    return (query or "").strip()


def _is_owner(user_id: int | None, admins: Any) -> bool:
    if user_id is None:
        return False
    if isinstance(admins, AdminRegistry):
        return admins.is_owner(user_id)
    return user_id in admins


def _temporary_admins_text(admins: list[dict[str, Any]], total: int, page: int) -> str:
    lines = [f"🛡 TEMPORARY ADMINS · {total} active"]
    for item in admins:
        expires_at = item.get("expires_at")
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            expiry = expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        else:
            expiry = "expiry unavailable"
        lines.append(f"• ID {item.get('user_id')} — expires {expiry}")
    if not admins:
        lines.append("No temporary admins are active.")
    lines.append(f"Page {page + 1}")
    return "\n".join(lines)


async def _temporary_admin_page(db: Database, page: int) -> tuple[list[dict[str, Any]], int, int]:
    page_size = 10
    total = await db.count_temporary_admins()
    last_page = max(0, (total - 1) // page_size)
    page = max(0, min(page, last_page))
    rows = await db.list_temporary_admins(page * page_size, page_size)
    return rows, total, page


async def _check_subscriptions(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database
) -> tuple[bool, str | None]:
    settings = await db.get_settings()
    if not settings.get("subscription", {}).get("enabled", False):
        return True, None
    channels = await db.get_subscriptions()
    if not channels:
        return True, None
    user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        return False, "Could not identify your Telegram account. Reopen the bot and try again."
    for channel in channels:
        title = str(channel.get("title") or channel.get("chat_id") or "required channel")[:80]
        try:
            member = await context.bot.get_chat_member(channel["chat_id"], user_id)
        except TelegramError:
            logger.exception("Could not verify subscription for channel %s.", channel.get("chat_id"))
            return False, (
                f"Could not verify {title}. Check the bot's channel access and saved chat ID."
            )
        if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
            logger.info(
                "Subscription check: user is not a member of channel %s (status=%s).",
                channel.get("chat_id"),
                member.status,
            )
            return False, f"Join {title} first, then tap Try Again."
    return True, None


async def _is_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database) -> bool:
    is_subscribed, _ = await _check_subscriptions(update, context, db)
    return is_subscribed


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
    payload = context.args[0] if context.args else ""
    file_key = None
    if payload.startswith("file_"):
        file_key = payload.removeprefix("file_")
        if not re.fullmatch(r"[0-9a-fA-F]{16}", file_key):
            await update.effective_message.reply_text("This file link is invalid or expired.")
            return

    if not _is_admin(user.id if user else None, admins) and not await _is_subscribed(update, context, db):
        if file_key:
            context.user_data["pending_file_key"] = file_key
        await _show_join_gate(update, context, db)
        return

    if file_key:
        item = await db.get_movie(file_key)
        if not item:
            await update.effective_message.reply_text("This indexed file is no longer available.")
            return
        await _deliver_movie(update, context, db, item)
        return

    description = settings.get("bot_description") or "Search Dahiya Movie Store"
    welcome_message = settings.get("welcome_message") or "Send a movie or series name to search the indexed files."
    text = (
        f"Welcome to Dahiya Movie Store.\n\n{description}\n\n"
        f"{welcome_message}"
    )
    markup = _welcome_keyboard(settings, _is_admin(user.id if user else None, admins))
    photo_id = settings.get("welcome_photo_file_id")
    if photo_id:
        try:
            if len(text) <= 1024:
                await update.effective_message.reply_photo(photo_id, caption=text, reply_markup=markup)
            else:
                await update.effective_message.reply_photo(photo_id, caption="Welcome to Dahiya Movie Store.")
                await update.effective_message.reply_text(text, reply_markup=markup)
            return
        except TelegramError:
            logger.exception("The saved welcome photo could not be sent.")
    await update.effective_message.reply_text(text, reply_markup=markup)


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
    await update.effective_message.reply_text(
        "Dahiya Movie Store — Admin Panel",
        reply_markup=admin_home(settings),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("admin_state", None)
    context.user_data.pop("temporary_admin_duration_seconds", None)
    await update.effective_message.reply_text("Current action cancelled.")


def _welcome_keyboard(settings: dict[str, Any], is_admin: bool) -> InlineKeyboardMarkup | None:
    rows = []
    welcome_button = settings.get("welcome_button")
    if isinstance(welcome_button, dict) and welcome_button.get("label") and welcome_button.get("url"):
        rows.append([InlineKeyboardButton(welcome_button["label"][:64], url=welcome_button["url"])])
    for item in (settings.get("custom_reply_buttons") or [])[:8]:
        if isinstance(item, dict) and item.get("label") and item.get("url"):
            rows.append([InlineKeyboardButton(item["label"][:64], url=item["url"])])
    if is_admin:
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="adm:home")])
    return InlineKeyboardMarkup(rows) if rows else None


def _not_found_keyboard(settings: dict[str, Any]) -> InlineKeyboardMarkup | None:
    item = settings.get("not_found_button")
    if isinstance(item, dict) and item.get("label") and item.get("url"):
        return InlineKeyboardMarkup([[InlineKeyboardButton(item["label"][:64], url=item["url"])]])
    return None


async def _bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    username = context.bot_data.get("bot_username")
    if not username:
        bot_user = await context.bot.get_me()
        username = bot_user.username
        if not username:
            raise RuntimeError("Telegram did not return a username for this bot.")
        context.bot_data["bot_username"] = username
    return str(username).lstrip("@")


def _result_line(item: dict[str, Any], bot_username: str) -> str:
    label = item.get("filename") or item.get("title") or "Indexed file"
    details = []
    size = item.get("size")
    if isinstance(size, (int, float)) and size > 0:
        amount, unit = float(size), "B"
        for next_unit in ("KB", "MB", "GB", "TB"):
            if amount < 1024:
                break
            amount /= 1024
            unit = next_unit
        details.append(f"{amount:.1f} {unit}")
    if item.get("year"):
        details.append(str(item["year"]))
    details.extend(item.get("languages") or [])
    if item.get("quality"):
        details.append(str(item["quality"]))
    if item.get("season") is not None:
        details.append(f"S{item['season']}")
    if item.get("episode") is not None:
        details.append(f"E{item['episode']}")
    suffix = f" · {' · '.join(details)[:120]}" if details else ""
    file_key = str(
        item.get("file_key")
        or movie_key(item["source_chat_id"], int(item["source_message_id"]))
    )
    url = escape(f"https://t.me/{bot_username}?start=file_{file_key}", quote=True)
    return f'🔹 <a href="{url}">{escape(str(label)[:200])}</a>{escape(suffix)}'


async def _schedule_result_auto_delete(
    context: ContextTypes.DEFAULT_TYPE,
    db: Database,
    message: Any,
    settings: dict[str, Any],
    request_message: Any | None = None,
) -> None:
    auto_delete = settings.get("auto_delete", {})
    if not auto_delete.get("enabled") or not message:
        return
    seconds = max(5, int(auto_delete.get("seconds", 300)))
    existing = await db.get_delivery(message.chat_id, message.message_id)
    status_id = existing.get("status_message_id") if existing else None
    status_text = f"⏳ AUTO-DELETE IN {seconds} SECONDS"
    if status_id:
        try:
            await context.bot.edit_message_text(
                chat_id=message.chat_id,
                message_id=status_id,
                text=status_text,
            )
        except TelegramError:
            status_id = None
    if not status_id:
        status = await context.bot.send_message(
            chat_id=message.chat_id,
            text=status_text,
            reply_to_message_id=message.message_id,
        )
        status_id = status.message_id
    await db.make_delivery(
        message.chat_id,
        message.message_id,
        status_id,
        utc_now() + timedelta(seconds=seconds),
        request_message_id=getattr(request_message, "message_id", None),
    )


async def _send_results(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db: Database,
    page: int = 0,
    search_data: dict[str, Any] | None = None,
    request_message: Any | None = None,
) -> None:
    if search_data is None:
        search_data = _search_state(update, context)
    hits: list[dict[str, Any]] = search_data.get("search_hits", [])
    filters = search_data.get("search_filters", {})
    for key, selected in filters.items():
        if key == "languages":
            hits = [item for item in hits if selected in (item.get("languages") or [])]
        elif key == "year":
            hits = [item for item in hits if str(item.get("year")) == selected]
        elif key == "quality":
            hits = [item for item in hits if (item.get("quality") or "") == selected]
        elif key == "season":
            hits = [item for item in hits if str(item.get("season")) == selected]
        elif key == "episode":
            hits = [item for item in hits if str(item.get("episode")) == selected]

    page = max(0, page)
    if hits:
        page = min(page, (len(hits) - 1) // PAGE_SIZE)
    start = page * PAGE_SIZE
    page_items = hits[start : start + PAGE_SIZE]
    search_data["current_page"] = page
    search_data["visible_total"] = len(hits)
    search_data["visible_file_keys"] = [item["file_key"] for item in page_items]
    filter_values = {
        "languages": sorted({lang for item in hits for lang in item.get("languages", [])}),
        "year": sorted({str(item["year"]) for item in hits if item.get("year") is not None}, reverse=True),
        "quality": sorted({item["quality"] for item in hits if item.get("quality")}),
        "episode": sorted({str(item["episode"]) for item in hits if item.get("episode") is not None}),
        "season": sorted({str(item["season"]) for item in hits if item.get("season") is not None}),
    }
    search_data["filter_values"] = filter_values
    if not hits:
        settings = await db.get_settings()
        message = settings.get("not_found_message") or "Sorry, this movie was not found."
        if update.callback_query:
            await update.callback_query.edit_message_text(message, reply_markup=_not_found_keyboard(settings))
            result_message = update.callback_query.message
        else:
            result_message = await update.effective_message.reply_text(
                message,
                reply_markup=_not_found_keyboard(settings),
            )
        await _schedule_result_auto_delete(
            context,
            db,
            result_message,
            settings,
            request_message=request_message,
        )
        return

    query = search_data.get("last_search", "")
    settings = await db.get_settings()
    header = _result_header(
        str(query),
        str(search_data.get("search_requested_by") or "Unknown"),
        float(search_data.get("search_elapsed_seconds") or 0),
        len(hits),
        str(settings.get("powered_by") or "Dahiya Movie Store | Search Zone"),
    )
    lines = []
    used_chars = len(header)
    bot_username = await _bot_username(context)
    for item in page_items:
        line = _result_line(item, bot_username)
        if used_chars + len(line) + 1 > 3800:
            break
        lines.append(line)
        used_chars += len(line) + 1
    if len(lines) < len(page_items):
        lines.append("More files are available on the next page.")
    summary = header + "\n".join(lines)
    keyboard = results_keyboard(page_items, page, len(hits), filter_values)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                summary,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                raise
        result_message = update.callback_query.message
    else:
        result_message = await update.effective_message.reply_text(
            summary,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    chat = update.effective_chat
    if chat and chat.type != ChatType.PRIVATE and getattr(result_message, "message_id", None) is not None:
        states = context.chat_data.setdefault("_search_results", {})
        states[str(result_message.message_id)] = search_data
        while len(states) > 100:
            states.pop(next(iter(states)))
    await _schedule_result_auto_delete(
        context,
        db,
        result_message,
        settings,
        request_message=request_message,
    )


def _result_header(
    query: str,
    requested_by: str,
    elapsed_seconds: float,
    total: int,
    powered_by: str,
) -> str:
    return (
        "✨ <b>THE RESULTS FOR</b> ✨\n"
        f"🏷️ <b>{escape(query[:120])}</b>\n"
        f"👤 <b>Requested By:</b> {escape(requested_by[:80])}\n"
        f"⚡ <b>Result shown in:</b> {max(0.0, elapsed_seconds):.2f} seconds\n"
        f"⭐ <b>Powered by:</b> {escape(powered_by[:120])}\n"
        "🔍 <b>Search Zone</b>\n"
        f"<i>{total} indexed file(s) found. Select a file or filter.</i>\n\n"
    )


async def _run_search(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database, query: str) -> None:
    query = query.strip()[:120]
    if not query:
        await update.effective_message.reply_text("Send a movie or series name to search.")
        return
    started = time.perf_counter()
    user = update.effective_user
    message = update.effective_message
    if message and getattr(message, "sender_chat", None) is not None:
        user = None
    if user:
        await db.register_member(user)
        await db.record_request(user, query)
    settings = await db.get_settings()
    hits = await db.search_movies(
        query,
        fuzzy_enabled=settings.get("fuzzy_search_enabled", True),
    )
    chat = update.effective_chat
    requested_by = (
        " ".join(filter(None, [user.first_name, user.last_name]))
        or user.username
        or str(user.id)
        if user
        else (getattr(chat, "title", None) or "Channel")
    )
    chat = update.effective_chat
    search_data = context.user_data if not chat or chat.type == ChatType.PRIVATE else {}
    search_data["last_search"] = query
    search_data["search_hits"] = hits
    search_data["search_filters"] = {}
    search_data["search_requested_by"] = requested_by
    search_data["search_elapsed_seconds"] = time.perf_counter() - started
    chat = getattr(update, "effective_chat", None)
    request_message = (
        message
        if message and (not chat or chat.type != ChatType.CHANNEL)
        else None
    )
    await _send_results(
        update,
        context,
        db,
        search_data=search_data,
        request_message=request_message,
    )


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


def _parse_link_button(value: str) -> dict[str, str] | None:
    parts = [part.strip() for part in value.split("|", 1)]
    if len(parts) != 2 or not parts[0] or len(parts[0]) > 64:
        return None
    url = _valid_url(parts[1])
    if not url:
        return None
    return {"label": parts[0], "url": url}


async def _prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str, text: str) -> None:
    context.user_data["admin_state"] = state
    await update.callback_query.message.reply_text(text)


def _subscription_count_text(channels: list[dict[str, Any]]) -> str:
    return f"Total links: {len(channels)}"


def _group_added_by_label(group: dict[str, Any]) -> str:
    name = str(group.get("added_by_name") or "").strip()
    username = str(group.get("added_by_username") or "").strip().lstrip("@")
    user_id = group.get("added_by_id")
    if name and username:
        return f"{name} (@{username})"
    if username:
        return f"@{username}"
    if name:
        return name
    if user_id:
        return f"Telegram user {user_id}"
    return "Not recorded"


def _index_job_status_line(title: str, job: dict[str, Any]) -> str:
    status = str(job.get("status", "unknown")).upper()
    if "files_processed" in job:
        total = job.get("files_total")
        total_label = str(total) if total is not None else "?"
        line = (
            f"• {title[:70]}: {status} — "
            f"{job.get('files_processed', 0)}/{total_label} files processed; "
            f"{job.get('indexed', 0)} indexed, {job.get('skipped', 0)} skipped, "
            f"{job.get('errors', 0)} errors"
        )
    else:
        line = (
            f"• {title[:70]}: {status} — {job.get('indexed', 0)} files indexed; "
            "total unavailable for this older scan"
        )
    if job.get("error"):
        line += f" — {str(job['error'])[:100]}"
    return line


async def _handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    db, indexer, admins = _services(context)
    query = update.callback_query
    user = update.effective_user
    if not _is_admin(user.id if user else None, admins):
        await query.answer("Not allowed.", show_alert=True)
        return
    admin_management_action = (
        data == "adm:admins"
        or data.startswith("adm:admins:page:")
        or data.startswith("adm:tempadmin:")
    )
    if admin_management_action and (
        not _is_owner(user.id if user else None, admins)
        or not update.effective_chat
        or update.effective_chat.type != ChatType.PRIVATE
    ):
        await query.answer(
            "Only a configured owner can manage admins in the private chat.",
            show_alert=True,
        )
        return
    await db.register_member(user)
    await query.answer()
    settings = await db.get_settings()

    if data == "adm:home":
        await query.edit_message_text(
            "👑 DAHIYA MOVIE STORE — ADMIN PANEL",
            reply_markup=admin_home(settings),
        )
    elif data == "adm:admins" or data.startswith("adm:admins:page:"):
        try:
            page = int(data.rsplit(":", 1)[1]) if data != "adm:admins" else 0
        except ValueError:
            page = 0
        rows, total, page = await _temporary_admin_page(db, page)
        await query.edit_message_text(
            _temporary_admins_text(rows, total, page),
            reply_markup=temporary_admins_keyboard(rows, page, total),
        )
    elif data.startswith("adm:tempadmin:add:"):
        try:
            duration_seconds = int(data.rsplit(":", 1)[1])
        except ValueError:
            duration_seconds = 0
        if duration_seconds not in TEMP_ADMIN_DURATIONS:
            await query.edit_message_text(
                "Invalid temporary-admin duration.",
                reply_markup=admin_home(settings),
            )
            return
        context.user_data["admin_state"] = "temporary_admin_id"
        context.user_data["temporary_admin_duration_seconds"] = duration_seconds
        await query.message.reply_text(
            f"Send the numeric Telegram user ID to grant admin access for "
            f"{TEMP_ADMIN_DURATIONS[duration_seconds]}. The grant expires automatically."
        )
    elif data.startswith("adm:tempadmin:confirm_remove:"):
        match = re.fullmatch(r"adm:tempadmin:confirm_remove:(\d+):(\d+)", data)
        if not match:
            await query.edit_message_text(
                "That temporary-admin entry is invalid.",
                reply_markup=admin_home(settings),
            )
            return
        user_id, page = (int(value) for value in match.groups())
        await query.edit_message_text(
            f"Remove temporary admin ID {user_id}?",
            reply_markup=temporary_admin_remove_confirmation(user_id, page),
        )
    elif data.startswith("adm:tempadmin:remove:"):
        match = re.fullmatch(r"adm:tempadmin:remove:(\d+):(\d+)", data)
        if not match:
            await query.edit_message_text(
                "That temporary-admin entry is invalid.",
                reply_markup=admin_home(settings),
            )
            return
        target_id, page = (int(value) for value in match.groups())
        if _is_owner(target_id, admins):
            await query.edit_message_text(
                "Configured owners cannot be removed here.",
                reply_markup=admin_home(settings),
            )
            return
        removed = await db.remove_temporary_admin(target_id)
        if isinstance(admins, AdminRegistry):
            admins.remove_temporary_admin(target_id)
        rows, total, page = await _temporary_admin_page(db, page)
        await query.edit_message_text(
            f"{'Temporary admin removed.' if removed else 'That temporary admin was not found.'}\n\n"
            + _temporary_admins_text(rows, total, page),
            reply_markup=temporary_admins_keyboard(rows, page, total),
        )
    elif data == "adm:toggle_group_search":
        enabled = not settings.get("group_search_enabled", True)
        await db.update_settings({"group_search_enabled": enabled})
        updated = await db.get_settings()
        await query.edit_message_text(
            f"Group search is now {'ON' if enabled else 'OFF'}.",
            reply_markup=admin_home(updated),
        )
    elif data == "adm:toggle_bot":
        enabled = not settings.get("bot_enabled", True)
        await db.update_settings({"bot_enabled": enabled})
        await query.edit_message_text(
            "👑 DAHIYA MOVIE STORE — ADMIN PANEL",
            reply_markup=admin_home(await db.get_settings()),
        )
    elif data == "adm:dashboard":
        stats = await db.request_stats()
        members = await db.count_members()
        active = await db.active_member_count(utc_now() - timedelta(minutes=5))
        recent = "\n".join(
            f"• {row.get('name') or row.get('username') or row['user_id']} ({row['user_id']}): {row.get('query', '')}"
            for row in stats["latest"]
        ) or "No requests yet."
        text = (
            "📊 LIVE DASHBOARD\n"
            f"👥 Active in last 5 min: {active}\nMembers: {members}\n"
            f"🔎 Search requests: {stats['requests']}\n"
            f"📁 Indexed files: {stats['indexed']}\nIndex groups: {stats['sources']}\n\n"
            f"Recent requests:\n{recent}"
        )
        await query.edit_message_text(
            text[:3900],
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Live Users & Activity", callback_data="adm:live"),
                     InlineKeyboardButton("User Search Requests", callback_data="adm:requests:0")],
                    [InlineKeyboardButton("Back", callback_data="adm:home")],
                ]
            ),
        )
    elif data == "adm:live":
        since = utc_now() - timedelta(minutes=5)
        active = await db.active_member_count(since)
        recent = await db.active_members(since, 10)
        lines = [
            f"• {' '.join(filter(None, [row.get('first_name'), row.get('last_name')])) or row.get('username') or row['user_id']} — last request: {row.get('last_request') or '—'}"
            for row in recent
        ]
        text = (
            "👥 LIVE USERS & ACTIVITY\n"
            "Active means a message or request in the last 5 minutes; Telegram does not expose a bot's full online list.\n\n"
            f"Active: {active}\n\n" + ("\n".join(lines) if lines else "No recent activity.")
        )
        await query.edit_message_text(
            text[:3900],
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Members", callback_data="adm:members:0")],
                    [InlineKeyboardButton("Back", callback_data="adm:home")],
                ]
            ),
        )
    elif data.startswith("adm:requests:"):
        offset = max(0, int(data.rsplit(":", 1)[1]))
        stats = await db.request_stats()
        rows = await db.recent_requests(offset, 10)
        lines = [
            f"• {row.get('name') or row.get('username') or row.get('user_id')} ({row.get('user_id')}): {row.get('query', '')}"
            for row in rows
        ]
        buttons = []
        if offset > 0:
            buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"adm:requests:{max(0, offset - 10)}"))
        if offset + 10 < stats["requests"]:
            buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"adm:requests:{offset + 10}"))
        rows_markup = [buttons] if buttons else []
        rows_markup.append([InlineKeyboardButton("Back", callback_data="adm:home")])
        text = f"🔎 USER SEARCH REQUESTS · {stats['requests']} total\n\n" + ("\n".join(lines) if lines else "No requests yet.")
        await query.edit_message_text(text[:3900], reply_markup=InlineKeyboardMarkup(rows_markup))
    elif data == "adm:groups":
        groups = await db.get_bot_groups()
        total_groups = await db.count_bot_groups()
        shown_groups = groups[:20]
        rows = [
            [InlineKeyboardButton(
                f"Leave {item.get('title') or item['chat_id']}"[:55],
                callback_data=f"adm:group_leave_confirm:{item['chat_id']}",
            )]
            for item in shown_groups
        ]
        rows.append([InlineKeyboardButton("Back", callback_data="adm:home")])
        group_lines = [
            f"• {str(item.get('title') or item['chat_id'])[:45]} — added by: "
            f"{_group_added_by_label(item)[:80]}"
            for item in shown_groups
        ]
        text = f"👨‍👩‍👧 BOT GROUPS · {total_groups} total\n\n"
        if group_lines:
            text += "\n".join(group_lines)
            if total_groups > len(shown_groups):
                text += f"\n\nShowing first {len(shown_groups)} groups."
            text += "\n\nTap a group below to remove the bot."
        else:
            text += "The bot has not recorded any groups yet."
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
    elif data.startswith("adm:group_leave_confirm:"):
        chat_id = data.rsplit(":", 1)[1]
        await query.edit_message_text(
            f"Remove the bot from group {chat_id}?",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("Confirm leave", callback_data=f"adm:group_leave:{chat_id}"),
                    InlineKeyboardButton("Cancel", callback_data="adm:groups"),
                ]]
            ),
        )
    elif data.startswith("adm:group_leave:"):
        chat_id = data.rsplit(":", 1)[1]
        try:
            await context.bot.leave_chat(int(chat_id))
            await db.remove_bot_group(chat_id)
            result = "The bot left the group."
        except TelegramError:
            logger.exception("Could not leave bot group %s.", chat_id)
            result = "Telegram could not remove the bot from that group."
        await query.edit_message_text(
            result,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="adm:groups")]]),
        )
    elif data == "adm:duplicates":
        groups = await db.duplicate_groups()
        if not groups:
            await query.edit_message_text(
                "🧾 DUPLICATE FILES\nNo duplicate file IDs were found in the current index.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="adm:home")]]),
            )
        else:
            lines = []
            rows = []
            for group in groups[:10]:
                files = group.get("files") or []
                keeper = files[0] if files else {}
                lines.append(
                    f"• {keeper.get('filename') or keeper.get('title') or 'File'} — {group.get('count', 0)} copies"
                )
                for item in files[1:4]:
                    rows.append(
                        [InlineKeyboardButton(
                            f"Review duplicate: {item.get('filename') or item.get('title') or 'file'}"[:60],
                            callback_data=f"adm:dupe_confirm:{item.get('file_key', '')}",
                        )]
                    )
            rows.append([InlineKeyboardButton("Back", callback_data="adm:home")])
            await query.edit_message_text(
                "🧾 DUPLICATE FILES\nRemoving an entry only removes it from this bot's index; "
                "the original Telegram message remains.\n\n" + "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(rows),
            )
    elif data.startswith("adm:dupe_confirm:"):
        file_key = data[len("adm:dupe_confirm:"):]
        item = await db.get_movie(file_key)
        if not item:
            await query.edit_message_text(
                "That indexed file is no longer available.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="adm:duplicates")]]),
            )
        else:
            label = item.get("filename") or item.get("title") or file_key
            await query.edit_message_text(
                f"Remove this duplicate from the bot index?\n\n{escape(str(label)[:300])}\n\n"
                "The original Telegram message will not be deleted.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Confirm remove", callback_data=f"adm:dupe_delete:{file_key}"),
                      InlineKeyboardButton("Cancel", callback_data="adm:duplicates")]]
                ),
                parse_mode="HTML",
            )
    elif data.startswith("adm:dupe_delete:"):
        file_key = data[len("adm:dupe_delete:"):]
        removed = await db.delete_movie_index(file_key)
        await query.edit_message_text(
            "Duplicate index entry removed." if removed else "That index entry was already removed.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to duplicates", callback_data="adm:duplicates")]]),
        )
    elif data == "adm:index_status":
        sources = await db.get_sources()
        lines = []
        control_buttons = []
        for source in sources[:30]:
            job = await db.get_latest_index_job(source["chat_id"])
            title = source.get("title") or source["chat_id"]
            if job:
                line = _index_job_status_line(title, job)
                chat_id = str(source["chat_id"])
                if (
                    job.get("status") == "running"
                    and indexer
                    and chat_id in indexer.running_chats
                ):
                    control_buttons.append(
                        InlineKeyboardButton(
                            f"⏸ Pause · {title}"[:55],
                            callback_data=f"adm:index_pause:{chat_id}",
                        )
                    )
                elif job.get("status") in {"running", "failed", "paused"}:
                    control_buttons.append(
                        InlineKeyboardButton(
                            f"▶️ Resume · {title}"[:55],
                            callback_data=f"adm:index_resume:{chat_id}",
                        )
                    )
            else:
                line = f"• {title}: no historical scan recorded"
            lines.append(line)
        text = "📈 INDEXING PROGRESS\n" + ("\n".join(lines) if lines else "No index sources configured.")
        rendered_text = text[:3900]
        keyboard_rows = [[InlineKeyboardButton("🔄 Refresh", callback_data="adm:index_status")]]
        if len(control_buttons) == 1:
            keyboard_rows[0].append(control_buttons[0])
        else:
            keyboard_rows.extend([[button] for button in control_buttons])
        keyboard_rows.append([InlineKeyboardButton("Back", callback_data="adm:sources")])
        try:
            await query.edit_message_text(
                rendered_text,
                reply_markup=InlineKeyboardMarkup(keyboard_rows),
            )
        except BadRequest as exc:
            if not _is_message_not_modified_error(exc):
                raise
    elif data.startswith("adm:index_pause:"):
        chat_id = data[len("adm:index_pause:"):]
        if not indexer or not indexer.request_pause(chat_id):
            message = (
                "No active scan is running for this source. Refresh the progress screen; "
                "if the scan stopped earlier, use Resume."
            )
        else:
            message = (
                "⏸ Pause requested. The current file will finish and save its checkpoint, "
                "then indexing will stop. Refresh shortly to confirm it is paused."
            )
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔄 Refresh", callback_data="adm:index_status")],
                    [InlineKeyboardButton("Back", callback_data="adm:sources")],
                ]
            ),
        )
    elif data.startswith("adm:index_resume:"):
        chat_id = data[len("adm:index_resume:"):]
        if not indexer:
            await query.edit_message_text(
                "Historical indexing is not available in this session.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Back", callback_data="adm:index_status")]]
                ),
            )
            return
        await query.edit_message_text(
            "Resuming from the saved checkpoint. Already indexed messages will be skipped."
        )
        asyncio.create_task(
            indexer.scan_chat(
                chat_id,
                query.message.chat_id,
                resume=True,
            )
        )
    elif data == "adm:cleanup":
        counts = await db.index_data_counts()
        await query.edit_message_text(
            "🧹 INDEXED FILE CLEANUP\n"
            f"Indexed files/videos: {counts['files']}\n"
            f"Saved scan checkpoints: {counts['checkpoints']}\n\n"
            "Confirming removes only the bot's indexed file/video records and scan checkpoints. "
            "Original Telegram files, sources, settings, members, and subscriptions stay unchanged.\n\n"
            "Delete this index data?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Yes, delete index", callback_data="adm:cleanup:confirm"),
                        InlineKeyboardButton("❌ No", callback_data="adm:home"),
                    ],
                ]
            ),
        )
    elif data == "adm:cleanup:confirm":
        if indexer and indexer.running_chats:
            await query.edit_message_text(
                "Cleanup cancelled: a historical scan is still running. Wait for it to stop, then try again. No data was deleted.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Back", callback_data="adm:home")]]
                ),
            )
            return
        removed = await db.clear_index_data()
        await query.edit_message_text(
            f"Cleanup complete: deleted {removed['files']} indexed file/video record(s) "
            f"and {removed['checkpoints']} scan checkpoint(s). "
            "Original Telegram files and all bot settings, sources, users, and subscriptions were preserved.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="adm:home")]]),
        )
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
    elif data == "adm:toggle_fuzzy":
        enabled = not settings.get("fuzzy_search_enabled", True)
        await db.update_settings({"fuzzy_search_enabled": enabled})
        await query.edit_message_text(
            f"Fuzzy search is now {'ON' if enabled else 'OFF'}.",
            reply_markup=settings_menu(await db.get_settings()),
        )
    elif data == "adm:timers":
        auto = settings.get("auto_delete", {})
        await query.edit_message_text(
            "⏱ TIMERS & COUNTDOWN\nThe timer starts when a search result is sent. It can be changed for future results.",
            reply_markup=timer_menu(auto.get("seconds", 300), auto.get("enabled", False)),
        )
    elif data == "adm:toggle_autodelete":
        enabled = not settings.get("auto_delete", {}).get("enabled", False)
        await db.update_settings({"auto_delete.enabled": enabled})
        await query.edit_message_text(
            f"Auto-delete is now {'ON' if enabled else 'OFF'}.",
            reply_markup=timer_menu(
                (await db.get_settings()).get("auto_delete", {}).get("seconds", 300),
                enabled,
            ),
        )
    elif data == "adm:toggle_new_index":
        enabled = not settings.get("new_indexing_enabled", True)
        await db.update_settings({"new_indexing_enabled": enabled})
        await query.edit_message_text(
            f"Automatic indexing of new channel files is now {'ON' if enabled else 'OFF'}.",
            reply_markup=source_list(await db.get_sources(), enabled),
        )
    elif data == "adm:toggle_subscription":
        enabled = not settings.get("subscription", {}).get("enabled", False)
        await db.update_settings({"subscription.enabled": enabled})
        channels = await db.get_subscriptions()
        await query.edit_message_text(
            f"Force-subscription is now {'ON' if enabled else 'OFF'}.\n{_subscription_count_text(channels)}",
            reply_markup=subscription_list(enabled),
        )
    elif data == "adm:timer":
        seconds = settings.get("auto_delete", {}).get("seconds", 300)
        await query.edit_message_text(
            "Choose an auto-delete duration:",
            reply_markup=timer_menu(seconds, settings.get("auto_delete", {}).get("enabled", False)),
        )
    elif data.startswith("adm:set_timer:"):
        seconds = int(data.rsplit(":", 1)[1])
        await db.update_settings({"auto_delete.seconds": seconds})
        await query.edit_message_text(
            f"Auto-delete duration set to {seconds} seconds.",
            reply_markup=timer_menu(seconds, settings.get("auto_delete", {}).get("enabled", False)),
        )
    elif data == "adm:custom_timer":
        await _prompt(update, context, "timer", "Send a custom duration in seconds (5–86400).")
    elif data == "adm:description":
        await _prompt(update, context, "description", "Send the new bot description (up to 512 characters).")
    elif data == "adm:photo":
        await _prompt(update, context, "photo", "Send a JPG/PNG photo. It will update the bot profile and the welcome image.")
    elif data == "adm:powered_by":
        current = settings.get("powered_by") or "Dahiya Movie Store | Search Zone"
        await query.edit_message_text(
            f"Current Powered by text:\n\n{escape(str(current))}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Edit Powered by", callback_data="adm:edit_powered_by")],
                 [InlineKeyboardButton("Reset", callback_data="adm:reset_powered_by")],
                 [InlineKeyboardButton("Back", callback_data="adm:settings")]]
            ),
            parse_mode="HTML",
        )
    elif data == "adm:edit_powered_by":
        await _prompt(update, context, "powered_by", "Send the Powered by text (up to 120 characters).")
    elif data == "adm:reset_powered_by":
        await db.update_settings({"powered_by": "Dahiya Movie Store | Search Zone"})
        await query.edit_message_text("Powered by text reset.", reply_markup=settings_menu(await db.get_settings()))
    elif data == "adm:welcome":
        current = settings.get("welcome_message")
        if not current:
            current = (
                "Built-in default:\n"
                f"{DEFAULT_SETTINGS['welcome_message']}"
            )
        await query.edit_message_text(
            f"Current welcome message:\n\n{current}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Edit message", callback_data="adm:edit_welcome"),
                     InlineKeyboardButton("Reset", callback_data="adm:reset_welcome"),
                     InlineKeyboardButton("Clear", callback_data="adm:clear_welcome")],
                    [InlineKeyboardButton("Set URL button", callback_data="adm:set_welcome_button"),
                     InlineKeyboardButton("Clear URL button", callback_data="adm:clear_welcome_button")],
                    [InlineKeyboardButton("Back", callback_data="adm:settings")],
                ]
            ),
        )
    elif data == "adm:edit_welcome":
        await _prompt(update, context, "welcome_message", "Send the welcome text users should see (up to 1000 characters).")
    elif data == "adm:reset_welcome":
        await db.update_settings({"welcome_message": DEFAULT_SETTINGS["welcome_message"]})
        await query.edit_message_text(
            "Welcome message reset to the built-in default.",
            reply_markup=settings_menu(await db.get_settings()),
        )
    elif data == "adm:clear_welcome":
        await db.update_settings({"welcome_message": ""})
        await query.edit_message_text(
            "Custom welcome text cleared. The built-in default will be shown.",
            reply_markup=settings_menu(await db.get_settings()),
        )
    elif data == "adm:welcome_button":
        current = settings.get("welcome_button")
        label = f"{current.get('label')} | {current.get('url')}" if isinstance(current, dict) else "Not set"
        await query.edit_message_text(
            f"Welcome URL button: {label}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Set URL button", callback_data="adm:set_welcome_button"),
                     InlineKeyboardButton("Clear URL button", callback_data="adm:clear_welcome_button")],
                    [InlineKeyboardButton("Back", callback_data="adm:settings")],
                ]
            ),
        )
    elif data in {"adm:set_welcome_button", "adm:edit_welcome_button"}:
        await _prompt(update, context, "welcome_button", "Send button label and URL like: Join channel | https://t.me/example")
    elif data == "adm:clear_welcome_button":
        await db.update_settings({"welcome_button": None})
        await query.edit_message_text("Welcome URL button cleared.", reply_markup=settings_menu(await db.get_settings()))
    elif data == "adm:reply_buttons":
        buttons = settings.get("custom_reply_buttons") or []
        lines = [f"• {item.get('label')} — {item.get('url')}" for item in buttons if isinstance(item, dict)]
        rows = [
            [InlineKeyboardButton(f"Remove {item.get('label', 'button')}"[:60], callback_data=f"adm:remove_reply:{index}")]
            for index, item in enumerate(buttons[:8])
            if isinstance(item, dict)
        ]
        rows.append([InlineKeyboardButton("Add reply button", callback_data="adm:add_reply_button")])
        rows.append([InlineKeyboardButton("Back", callback_data="adm:settings")])
        await query.edit_message_text(
            "🔗 BOT REPLY BUTTONS\n" + ("\n".join(lines) if lines else "No custom buttons yet."),
            reply_markup=InlineKeyboardMarkup(rows),
        )
    elif data == "adm:add_reply_button":
        await _prompt(update, context, "reply_button", "Send button label and URL like: Help | https://example.com/help")
    elif data.startswith("adm:remove_reply:"):
        try:
            index = int(data.rsplit(":", 1)[1])
            buttons = list(settings.get("custom_reply_buttons") or [])
            buttons.pop(index)
        except (ValueError, IndexError):
            await query.edit_message_text(
                "That button is no longer available.",
                reply_markup=settings_menu(settings),
            )
            return
        await db.update_settings({"custom_reply_buttons": buttons})
        await query.edit_message_text("Reply button removed.", reply_markup=settings_menu(await db.get_settings()))
    elif data == "adm:notfound":
        current = settings.get("not_found_message", "")
        not_found_button = settings.get("not_found_button")
        button_label = not_found_button.get("label") if isinstance(not_found_button, dict) else "Not set"
        await query.edit_message_text(
            f"Current movie-not-found message:\n\n{current}\n\nButton: {button_label}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Edit message", callback_data="adm:edit_notfound"),
                     InlineKeyboardButton("Reset", callback_data="adm:reset_notfound"),
                     InlineKeyboardButton("Clear", callback_data="adm:clear_notfound")],
                    [InlineKeyboardButton("Set URL button", callback_data="adm:set_notfound_button"),
                     InlineKeyboardButton("Clear URL button", callback_data="adm:clear_notfound_button")],
                    [InlineKeyboardButton("Back", callback_data="adm:settings")],
                ]
            ),
        )
    elif data == "adm:edit_notfound":
        await _prompt(update, context, "notfound", "Send the replacement movie-not-found message.")
    elif data == "adm:set_notfound_button":
        await _prompt(update, context, "notfound_button", "Send button label and URL like: Request movie | https://example.com")
    elif data == "adm:clear_notfound_button":
        await db.update_settings({"not_found_button": None})
        await query.edit_message_text("Movie-not-found URL button cleared.", reply_markup=settings_menu(await db.get_settings()))
    elif data == "adm:reset_notfound":
        await db.update_settings({"not_found_message": "Sorry, this movie was not found. Please check the name and try again."})
        await query.edit_message_text("Movie-not-found message reset.", reply_markup=settings_menu(await db.get_settings()))
    elif data == "adm:clear_notfound":
        await db.update_settings({"not_found_message": ""})
        await query.edit_message_text("Custom message cleared. The built-in fallback will be used.", reply_markup=settings_menu(await db.get_settings()))
    elif data == "adm:sources":
        await query.edit_message_text(
            "📁 INDEX SOURCES\nRemoving a source does not erase its indexed records.",
            reply_markup=source_list(
                await db.get_sources(),
                settings.get("new_indexing_enabled", True),
            ),
        )
    elif data == "adm:add_source":
        await _prompt(update, context, "add_source", "Send the source chat ID (for example -1001234567890). Add this bot as an administrator with permission to read channel posts.")
    elif data.startswith("adm:del_source:"):
        chat_id = data[len("adm:del_source:"):]
        removed = await db.remove_source(chat_id)
        await query.edit_message_text(
            "Source removed. Existing indexed records were kept." if removed else "Source was not found.",
            reply_markup=source_list(
                await db.get_sources(),
                settings.get("new_indexing_enabled", True),
            ),
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
        await query.edit_message_text(
            f"Force-subscription links\n{_subscription_count_text(channels)}",
            reply_markup=subscription_list(enabled),
        )
    elif data == "adm:remove_sub":
        channels = await db.get_subscriptions()
        enabled = settings.get("subscription", {}).get("enabled", False)
        if not channels:
            await query.edit_message_text(
                f"There are no force-subscription links to remove.\n{_subscription_count_text(channels)}",
                reply_markup=subscription_list(enabled),
            )
        else:
            await query.edit_message_text(
                f"Choose the link to remove.\n{_subscription_count_text(channels)}",
                reply_markup=subscription_remove_list(channels),
            )
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
        channels = await db.get_subscriptions()
        updated_settings = await db.get_settings()
        await query.edit_message_text(
            f"{'Subscription link removed.' if removed else 'Link was not found.'}\n{_subscription_count_text(channels)}",
            reply_markup=subscription_list(updated_settings.get("subscription", {}).get("enabled", False)),
        )
    elif data == "adm:broadcast":
        context.user_data["admin_state"] = "broadcast_text"
        await query.message.reply_text("Send the broadcast text. If it contains a link, the bot will add an Open link button.")
    elif data == "adm:broadcast_skip":
        pending = context.user_data.pop("broadcast_text", "")
        context.user_data.pop("admin_state", None)
        sent, failed = await _broadcast(db, context, pending, None)
        await query.message.reply_text(f"Broadcast complete. Sent: {sent}; failed: {failed}.")


async def _handle_admin_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db: Database,
    admins: Any,
) -> bool:
    state = context.user_data.get("admin_state")
    if not state:
        return False
    text = (update.effective_message.text or "").strip()
    if state == "temporary_admin_id":
        user = update.effective_user
        if not _is_owner(user.id if user else None, admins):
            context.user_data.pop("admin_state", None)
            context.user_data.pop("temporary_admin_duration_seconds", None)
            await update.effective_message.reply_text("Only a configured owner can add temporary admins.")
            return True
        if not text.isascii() or not text.isdigit():
            await update.effective_message.reply_text("Send a numeric Telegram user ID.")
            return True
        target_id = int(text)
        if not 0 < target_id <= 2**63 - 1 or target_id == context.bot.id:
            await update.effective_message.reply_text("That Telegram user ID is not valid.")
            return True
        if _is_owner(target_id, admins):
            await update.effective_message.reply_text("Configured owners already have permanent admin access.")
            return True
        duration_seconds = context.user_data.get("temporary_admin_duration_seconds")
        if duration_seconds not in TEMP_ADMIN_DURATIONS:
            context.user_data.pop("admin_state", None)
            await update.effective_message.reply_text("The temporary-admin request expired. Start again from Admin Panel.")
            return True
        expires_at = utc_now() + timedelta(seconds=duration_seconds)
        await db.grant_temporary_admin(target_id, user.id, expires_at)
        if isinstance(admins, AdminRegistry):
            admins.add_temporary_admin(target_id, expires_at)
        context.user_data.pop("admin_state", None)
        context.user_data.pop("temporary_admin_duration_seconds", None)
        rows, total, page = await _temporary_admin_page(db, 0)
        await update.effective_message.reply_text(
            f"Temporary admin ID {target_id} added for {TEMP_ADMIN_DURATIONS[duration_seconds]}.",
            reply_markup=temporary_admins_keyboard(rows, page, total),
        )
    elif state == "powered_by":
        if not text or len(text) > 120:
            await update.effective_message.reply_text("Send Powered by text between 1 and 120 characters.")
            return True
        await db.update_settings({"powered_by": text})
        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text("Powered by text updated.", reply_markup=settings_menu(await db.get_settings()))
    elif state == "description":
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
    elif state == "welcome_message":
        if not text or len(text) > 1000:
            await update.effective_message.reply_text("Send welcome text between 1 and 1000 characters.")
            return True
        await db.update_settings({"welcome_message": text})
        context.user_data.pop("admin_state", None)
        await update.effective_message.reply_text(
            "Welcome message updated.",
            reply_markup=settings_menu(await db.get_settings()),
        )
    elif state in ("welcome_button", "reply_button", "notfound_button"):
        button = _parse_link_button(text)
        if not button:
            await update.effective_message.reply_text(
                "Use this format with a valid http/https link: Button label | https://example.com"
            )
            return True
        if state == "welcome_button":
            await db.update_settings({"welcome_button": button})
            result = "Welcome URL button saved."
        elif state == "notfound_button":
            await db.update_settings({"not_found_button": button})
            result = "Movie-not-found URL button saved."
        else:
            buttons = list((await db.get_settings()).get("custom_reply_buttons") or [])
            if len(buttons) >= 8:
                context.user_data.pop("admin_state", None)
                await update.effective_message.reply_text("You can keep up to 8 custom reply buttons.")
                return True
            buttons.append(button)
            await db.update_settings({"custom_reply_buttons": buttons})
            result = "Bot reply button saved. It will appear on the welcome screen."
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
            reply_markup=timer_menu(
                int(text),
                (await db.get_settings()).get("auto_delete", {}).get("enabled", False),
            ),
        )
    elif state == "add_source":
        source_id = text.split()[0]
        lookup_id: int | str
        try:
            lookup_id = int(source_id)
        except ValueError:
            if source_id.startswith("@"):
                lookup_id = source_id
            else:
                await update.effective_message.reply_text(
                    "Send a numeric chat ID like -1001234567890 or a public @channel username."
                )
                return True
        try:
            chat = await context.bot.get_chat(lookup_id)
            await db.save_source(
                chat.id,
                chat.title or chat.username or str(chat.id),
                chat.type,
                chat.username,
            )
            context.user_data.pop("admin_state", None)
            await update.effective_message.reply_text(
                f"Added {chat.title or chat.username or chat.id} to indexing sources. New posts will be indexed automatically when new indexing is ON.",
                reply_markup=source_list(
                    await db.get_sources(),
                    (await db.get_settings()).get("new_indexing_enabled", True),
                ),
            )
        except TelegramError:
            logger.exception("Could not add Telegram index source %s.", lookup_id)
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
            reply_markup=source_list(
                await db.get_sources(),
                (await db.get_settings()).get("new_indexing_enabled", True),
            ),
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
            channels = await db.get_subscriptions()
            updated_settings = await db.get_settings()
            await update.effective_message.reply_text(
                f"Required channel added.\n{_subscription_count_text(channels)}",
                reply_markup=subscription_list(updated_settings.get("subscription", {}).get("enabled", False)),
            )
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
    if _is_admin(user.id, admins) and await _handle_admin_text(update, context, db, admins):
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
    if not message:
        return

    query = await _search_command_text(message.text, context)
    if query is None:
        await _save_new_source_message(message, db)
        return

    settings = await db.get_settings()
    if not settings.get("group_search_enabled", True) or not settings.get("bot_enabled", True):
        return
    if not await _bot_is_group_admin(message, context, db):
        return
    if not query:
        await message.reply_text("Use /search <movie or series name>.")
        return
    await _run_search(update, context, db, query)


async def bot_membership_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    change = update.my_chat_member
    if not change:
        return
    db, _, _ = _services(context)
    status = change.new_chat_member.status
    if status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        existing = await db.get_bot_group(change.chat.id)
        actor = change.from_user
        added_by_id = existing.get("added_by_id") if existing else None
        added_by_name = existing.get("added_by_name") if existing else None
        added_by_username = existing.get("added_by_username") if existing else None
        if actor and not added_by_id:
            added_by_id = actor.id
            added_by_name = " ".join(filter(None, [actor.first_name, actor.last_name])) or None
            added_by_username = actor.username
        await db.save_bot_group(
            change.chat.id,
            change.chat.title or change.chat.username or str(change.chat.id),
            change.chat.type,
            str(status),
            added_by_id,
            added_by_name,
            added_by_username,
        )
    else:
        await db.remove_bot_group(change.chat.id)


async def _bot_is_group_admin(message: Any, context: ContextTypes.DEFAULT_TYPE, db: Database) -> bool:
    group = await db.get_bot_group(message.chat.id)
    if group and group.get("bot_status") == ChatMemberStatus.ADMINISTRATOR:
        return True

    # Refresh cached non-admin or missing status after a bot is promoted.
    try:
        membership = await context.bot.get_chat_member(message.chat.id, context.bot.id)
    except TelegramError:
        logger.exception("Could not verify the bot's admin status in chat %s.", message.chat.id)
        return False

    status = membership.status
    await db.save_bot_group(
        message.chat.id,
        message.chat.title or message.chat.username or str(message.chat.id),
        message.chat.type,
        str(status),
    )
    return status == ChatMemberStatus.ADMINISTRATOR


async def group_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, _, admins = _services(context)
    message = update.effective_message
    if not message:
        return
    user = update.effective_user
    anonymous_sender = getattr(message, "sender_chat", None) is not None
    search_user = None if anonymous_sender else user
    text = message.text or ""
    command_query = await _search_command_text(text, context)
    query = command_query if command_query is not None else (text if text and not text.startswith("/") else None)
    if query is not None and (anonymous_sender or user is None or not user.is_bot):
        settings = await db.get_settings()
        if not settings.get("group_search_enabled", True):
            return
        if not settings.get("bot_enabled", True) and not _is_admin(search_user.id if search_user else None, admins):
            return
        if not await _bot_is_group_admin(message, context, db):
            return
        if search_user and not _is_admin(search_user.id, admins) and not await _is_subscribed(update, context, db):
            await _show_join_gate(update, context, db)
            return
        if not query.strip():
            await message.reply_text("Use /search <movie or series name>.")
            return
        await _run_search(update, context, db, query)
        return
    await _save_new_source_message(message, db)


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
    search_data = _search_state(update, context)
    await db.register_member(user)
    if data == "sub:check":
        is_subscribed, reason = await _check_subscriptions(update, context, db)
        if not is_subscribed:
            await query.answer(reason or "Join the required channel(s) first.", show_alert=True)
            return
        await query.answer("Subscription confirmed.")
        pending_file_key = context.user_data.pop("pending_file_key", None)
        if pending_file_key:
            item = await db.get_movie(pending_file_key)
            if item:
                await _deliver_movie(update, context, db, item)
            else:
                await query.message.reply_text("This indexed file is no longer available.")
        else:
            await query.message.reply_text("You are verified. Send a movie or series name to search.")
    elif data == "search:back":
        await query.answer()
        search_data.pop("search_hits", None)
        await query.message.reply_text("Send a movie or series name to search.")
    elif data == "search:results":
        await query.answer()
        await _send_results(update, context, db, search_data=search_data)
    elif data.startswith("filter:"):
        await query.answer()
        kind = data.split(":", 1)[1]
        values = search_data.get("filter_values", {}).get(kind, [])
        if not values:
            await query.edit_message_text(
                f"No {kind} options are indexed for these results.",
                reply_markup=filter_options_keyboard(kind, []),
            )
            return
        await query.edit_message_text(
            f"Choose {kind}:",
            reply_markup=filter_options_keyboard(kind, values),
        )
    elif data.startswith("choose:"):
        _, kind, raw_index = data.split(":", 2)
        values = search_data.get("filter_values", {}).get(kind, [])
        try:
            selected = values[int(raw_index)]
        except (IndexError, ValueError):
            await query.answer("That filter expired. Search again.", show_alert=True)
            return
        search_data.setdefault("search_filters", {})[kind] = selected
        await query.answer(f"Filter: {selected}")
        await _send_results(update, context, db, search_data=search_data)
    elif data == "page:picker":
        await query.answer()
        await query.edit_message_reply_markup(
            reply_markup=page_picker_keyboard(
                search_data.get("visible_total", len(search_data.get("search_hits", []))),
                search_data.get("current_page", 0),
            )
        )
    elif data.startswith("page:"):
        await query.answer()
        await _send_results(update, context, db, max(0, int(data.split(":", 1)[1])), search_data=search_data)
    elif data == "send_all":
        if not _is_admin(user.id if user else None, admins) and not await _is_subscribed(update, context, db):
            await _show_join_gate(update, context, db)
            return
        await query.answer()
        hits = search_data.get("search_hits", [])
        filters = search_data.get("search_filters", {})
        for key, selected in filters.items():
            if key == "languages":
                hits = [item for item in hits if selected in (item.get("languages") or [])]
            elif key == "year":
                hits = [item for item in hits if str(item.get("year")) == selected]
            elif key == "quality":
                hits = [item for item in hits if item.get("quality") == selected]
            elif key == "season":
                hits = [item for item in hits if str(item.get("season")) == selected]
            elif key == "episode":
                hits = [item for item in hits if str(item.get("episode")) == selected]
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
            request_message = (
                update.effective_message
                if not getattr(update, "callback_query", None)
                else None
            )
            await db.make_delivery(
                chat_id,
                copied.message_id,
                status_message.message_id,
                expires,
                request_message_id=getattr(request_message, "message_id", None),
            )
    except TelegramError:
        logger.exception("Bot API could not copy indexed message %s.", item.get("file_key"))
        url = _source_link(item)
        await update.effective_message.reply_text(
            "I could not copy this file. The bot may need access to the source channel.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Open source file", url=url)]]),
        )


def _is_message_not_modified_error(error: BaseException | None) -> bool:
    return isinstance(error, BadRequest) and "message is not modified" in str(error).lower()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_message_not_modified_error(context.error):
        return
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
    application.add_handler(ChatMemberHandler(bot_membership_update, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, channel_post))
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, group_post))
    application.add_error_handler(error_handler)

