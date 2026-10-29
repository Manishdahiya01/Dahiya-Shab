from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def admin_home(
    settings: dict[str, Any],
    can_manage_admins: bool = False,
) -> InlineKeyboardMarkup:
    bot_label = "Bot: ON" if settings.get("bot_enabled", True) else "Bot: OFF"
    group_replies_label = (
        f"Group replies: {'ON' if settings.get('group_search_enabled', True) else 'OFF'}"
    )
    rows = [
            [InlineKeyboardButton(f"👑 DAHIYA MOVIE STORE ADMIN · {bot_label}", callback_data="adm:dashboard")],
            [InlineKeyboardButton("📊 Dashboard", callback_data="adm:dashboard"),
             InlineKeyboardButton("📁 Index Sources", callback_data="adm:sources")],
            [InlineKeyboardButton("👥 Live Users & Activity", callback_data="adm:live"),
             InlineKeyboardButton("📣 Force Subscribe", callback_data="adm:subscription")],
            [InlineKeyboardButton("🔎 User Search Requests", callback_data="adm:requests:0"),
             InlineKeyboardButton("📢 Broadcast", callback_data="adm:broadcast")],
            [InlineKeyboardButton("👨‍👩‍👧 Bot Groups", callback_data="adm:groups"),
             InlineKeyboardButton(group_replies_label, callback_data="adm:toggle_group_search")],
            [InlineKeyboardButton("⏱ Timers & Countdown", callback_data="adm:timers"),
             InlineKeyboardButton("⚙️ Settings", callback_data="adm:settings")],
        ]
    if can_manage_admins:
        rows.insert(
            5,
            [InlineKeyboardButton("🛡 Temporary Admins", callback_data="adm:admins")],
        )
    return InlineKeyboardMarkup(rows)


def temporary_admins_keyboard(
    admins: list[dict[str, Any]],
    page: int,
    total: int,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"Remove ID {int(admin['user_id'])}",
                callback_data=f"adm:tempadmin:confirm_remove:{int(admin['user_id'])}:{page}",
            )
        ]
        for admin in admins
    ]
    rows.append(
        [
            InlineKeyboardButton("Add · 1 hour", callback_data="adm:tempadmin:add:3600"),
            InlineKeyboardButton("Add · 24 hours", callback_data="adm:tempadmin:add:86400"),
            InlineKeyboardButton("Add · 7 days", callback_data="adm:tempadmin:add:604800"),
        ]
    )
    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton("← Previous", callback_data=f"adm:admins:page:{page - 1}")
        )
    if (page + 1) * page_size < total:
        navigation.append(
            InlineKeyboardButton("Next →", callback_data=f"adm:admins:page:{page + 1}")
        )
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton("Back", callback_data="adm:home")])
    return InlineKeyboardMarkup(rows)


def temporary_admin_remove_confirmation(user_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Confirm remove",
                    callback_data=f"adm:tempadmin:remove:{int(user_id)}:{page}",
                ),
                InlineKeyboardButton("Cancel", callback_data=f"adm:admins:page:{page}"),
            ]
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
            [InlineKeyboardButton("Powered by branding", callback_data="adm:powered_by")],
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
            [InlineKeyboardButton("Indexing progress", callback_data="adm:index_status")],
            [InlineKeyboardButton("Back", callback_data="adm:home")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def subscription_list(enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"Force-sub: {'ON' if enabled else 'OFF'}", callback_data="adm:toggle_subscription")],
            [
                InlineKeyboardButton("➕ Add link", callback_data="adm:add_sub"),
                InlineKeyboardButton("➖ Remove link", callback_data="adm:remove_sub"),
            ],
            [InlineKeyboardButton("Back", callback_data="adm:home")],
        ]
    )


def subscription_remove_list(channels: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for start in range(0, len(channels), 2):
        rows.append(
            [
                InlineKeyboardButton(
                    f"🗑 {row.get('title') or row['chat_id']}"[:30],
                    callback_data=f"adm:del_sub:{row['chat_id']}",
                )
                for row in channels[start : start + 2]
            ]
        )
    rows.append([InlineKeyboardButton("⬅ Back to Force-sub", callback_data="adm:subscription")])
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
    total_pages = max(1, (total + 9) // 10)
    page = max(0, min(page, total_pages - 1))
    page_control = InlineKeyboardButton(
        "PAGE" if page == 0 else "PREVIOUS ←",
        callback_data="page:picker" if page == 0 else f"page:{page - 1}",
    )
    page_count = InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="page:picker")
    page_row = [page_control, page_count]
    if page + 1 < total_pages:
        page_row.append(InlineKeyboardButton("NEXT ➪", callback_data=f"page:{page + 1}"))

    rows = [
        [
            InlineKeyboardButton("Send All", callback_data="send_all"),
            InlineKeyboardButton("LANGUAGES", callback_data="filter:languages"),
            InlineKeyboardButton("YEARS", callback_data="filter:year"),
        ],
        [
            InlineKeyboardButton("QUALITY", callback_data="filter:quality"),
            InlineKeyboardButton("EPISODES", callback_data="filter:episode"),
            InlineKeyboardButton("SEASONS", callback_data="filter:season"),
        ],
        page_row,
    ]
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
    rows.append([InlineKeyboardButton("⬅ Back to results", callback_data="search:results")])
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
    rows.append([InlineKeyboardButton("⬅ Back to results", callback_data="search:results")])
    return InlineKeyboardMarkup(rows)

