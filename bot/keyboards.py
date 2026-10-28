from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def admin_home(settings: dict[str, Any]) -> InlineKeyboardMarkup:
    bot_label = "Bot: ON" if settings.get("bot_enabled", True) else "Bot: OFF"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"👑 DAHIYA MOVIE STORE ADMIN · {bot_label}", callback_data="adm:dashboard")],
            [InlineKeyboardButton("📊 Dashboard", callback_data="adm:dashboard"),
             InlineKeyboardButton("📁 Index Sources", callback_data="adm:sources")],
            [InlineKeyboardButton("👥 Live Users & Activity", callback_data="adm:live"),
             InlineKeyboardButton("📣 Force Subscribe", callback_data="adm:subscription")],
            [InlineKeyboardButton("🔎 User Search Requests", callback_data="adm:requests:0"),
             InlineKeyboardButton("📢 Broadcast", callback_data="adm:broadcast")],
            [InlineKeyboardButton("⏱ Timers & Countdown", callback_data="adm:timers"),
             InlineKeyboardButton("⚙️ Settings", callback_data="adm:settings")],
            [InlineKeyboardButton("🧹 Database Cleanup", callback_data="adm:cleanup")],
        ]
    )


def settings_menu(settings: dict[str, Any]) -> InlineKeyboardMarkup:
    auto = settings.get("auto_delete", {})
    sub = settings.get("subscription", {})
    new_index = settings.get("new_indexing_enabled", True)
    fuzzy = settings.get("fuzzy_search_enabled", True)
    timer_label = f"Auto-delete: {'ON' if auto.get('enabled') else 'OFF'}"
    sub_label = f"Force-sub: {'ON' if sub.get('enabled') else 'OFF'}"
    index_label = f"New indexing: {'ON' if new_index else 'OFF'}"
    fuzzy_label = f"Fuzzy search: {'ON' if fuzzy else 'OFF'}"
    bot_label = f"Bot: {'ON' if settings.get('bot_enabled', True) else 'OFF'}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(bot_label, callback_data="adm:toggle_bot")],
            [InlineKeyboardButton("Bot picture", callback_data="adm:photo"),
             InlineKeyboardButton("Bot description", callback_data="adm:description")],
            [InlineKeyboardButton("Welcome message", callback_data="adm:welcome"),
             InlineKeyboardButton("Welcome button", callback_data="adm:welcome_button")],
            [InlineKeyboardButton("Bot reply buttons", callback_data="adm:reply_buttons")],
            [InlineKeyboardButton("Movie-not-found", callback_data="adm:notfound")],
            [InlineKeyboardButton(timer_label, callback_data="adm:toggle_autodelete"),
             InlineKeyboardButton(f"Timer: {auto.get('seconds', 300)} sec", callback_data="adm:timer")],
            [InlineKeyboardButton(fuzzy_label, callback_data="adm:toggle_fuzzy"),
             InlineKeyboardButton(index_label, callback_data="adm:toggle_new_index")],
            [InlineKeyboardButton(sub_label, callback_data="adm:toggle_subscription")],
            [InlineKeyboardButton("Back", callback_data="adm:home")],
        ]
    )


def timer_menu(seconds: int, enabled: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"Auto-delete: {'ON' if enabled else 'OFF'}", callback_data="adm:toggle_autodelete")],
            [InlineKeyboardButton("30 sec", callback_data="adm:set_timer:30"),
             InlineKeyboardButton("2 min", callback_data="adm:set_timer:120"),
             InlineKeyboardButton("5 min", callback_data="adm:set_timer:300")],
            [InlineKeyboardButton(f"Custom ({seconds} sec)", callback_data="adm:custom_timer")],
            [InlineKeyboardButton("Back", callback_data="adm:settings")],
        ]
    )


def source_list(
    sources: list[dict[str, Any]],
    new_indexing_enabled: bool = True,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(f"Edit {row.get('title') or row['chat_id']}"[:30], callback_data=f"adm:edit_source:{row['chat_id']}"),
            InlineKeyboardButton("Remove", callback_data=f"adm:del_source:{row['chat_id']}"),
        ]
        for row in sources[:20]
    ]
    rows.extend(
        [
            [InlineKeyboardButton(
                f"🆕 New files: {'ON' if new_indexing_enabled else 'OFF'}",
                callback_data="adm:toggle_new_index",
            )],
            [InlineKeyboardButton("Add index group/channel", callback_data="adm:add_source")],
            [InlineKeyboardButton("Start old-file indexing", callback_data="adm:old_index")],
            [InlineKeyboardButton("Back", callback_data="adm:home")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def subscription_list(channels: list[dict[str, Any]], enabled: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"Remove {row.get('title') or row['chat_id']}"[:60], callback_data=f"adm:del_sub:{row['chat_id']}")]
        for row in channels[:20]
    ]
    rows.extend(
        [
            [InlineKeyboardButton(f"Force-sub: {'ON' if enabled else 'OFF'}", callback_data="adm:toggle_subscription")],
            [InlineKeyboardButton("Add required channel", callback_data="adm:add_sub")],
            [InlineKeyboardButton("Back", callback_data="adm:home")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def members_keyboard(offset: int, count: int, page_size: int = 15) -> InlineKeyboardMarkup:
    rows = []
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("Previous", callback_data=f"adm:members:{max(0, offset - page_size)}"))
    if offset + page_size < count:
        nav.append(InlineKeyboardButton("Next", callback_data=f"adm:members:{offset + page_size}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("Back", callback_data="adm:home")])
    return InlineKeyboardMarkup(rows)


def join_gate(channels: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for item in channels:
        if item.get("join_url"):
            rows.append([InlineKeyboardButton(f"Join {item.get('title', 'channel')}"[:60], url=item["join_url"])])
    rows.append([InlineKeyboardButton("🔄 Try Again", callback_data="sub:check")])
    return InlineKeyboardMarkup(rows)


def results_keyboard(
    items: list[dict[str, Any]],
    page: int,
    total: int,
    filter_values: dict[str, list[str]] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    filters = filter_values or {}
    filter_buttons = []
    for label, key in (
        ("🌐 Languages", "languages"),
        ("📅 Years", "year"),
        ("🎞 Quality", "quality"),
        ("📺 Episodes", "episode"),
        ("🍿 Seasons", "season"),
    ):
        values = filters.get(key, [])
        if values:
            filter_buttons.append(InlineKeyboardButton(label, callback_data=f"filter:{key}"))
    if filter_buttons:
        for start in range(0, len(filter_buttons), 2):
            rows.append(filter_buttons[start : start + 2])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page:{page - 1}"))
    if (page + 1) * 10 < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"page:{page + 1}"))
    total_pages = max(1, (total + 9) // 10)
    rows.insert(0, [InlineKeyboardButton("📤 Send All (up to 10)", callback_data="send_all")])
    rows.append([InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="page:picker")])
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔎 Back to search", callback_data="search:back")])
    return InlineKeyboardMarkup(rows)


def filter_options_keyboard(kind: str, values: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for start in range(0, len(values), 2):
        pair = values[start : start + 2]
        rows.append(
            [
                InlineKeyboardButton(value[:35], callback_data=f"choose:{kind}:{start + idx}")
                for idx, value in enumerate(pair)
            ]
        )
    rows.append([InlineKeyboardButton("Back", callback_data="search:results")])
    return InlineKeyboardMarkup(rows)


def page_picker_keyboard(total: int, current_page: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (total + 9) // 10)
    rows = []
    pages = range(min(total_pages, 10))
    for start in range(0, len(pages), 5):
        rows.append(
            [
                InlineKeyboardButton(
                    f"{page + 1}{' ✓' if page == current_page else ''}",
                    callback_data=f"page:{page}",
                )
                for page in list(pages)[start : start + 5]
            ]
        )
    rows.append([InlineKeyboardButton("Back to results", callback_data="search:results")])
    return InlineKeyboardMarkup(rows)

