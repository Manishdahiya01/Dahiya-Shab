from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_admin_panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dash"), InlineKeyboardButton("📁 Index Sources", callback_data="admin_index")],
        [InlineKeyboardButton("👥 Live Users & Activity", callback_data="admin_live"), InlineKeyboardButton("📢 Force Subscribe", callback_data="admin_fsub")],
        [InlineKeyboardButton("🔍 User Search Requests", callback_data="admin_requests"), InlineKeyboardButton("📡 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⏱️ Timers & Countdown", callback_data="admin_timer"), InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("🧹 Database Cleanup", callback_data="admin_cleanup")]
    ])

def get_index_sources_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 New Files Index", callback_data="idx_new"), InlineKeyboardButton("➕ Add Index Group", callback_data="idx_add")],
        [InlineKeyboardButton("➖ Remove Index Group", callback_data="idx_rem"), InlineKeyboardButton("📜 Old Index Group (Season String)", callback_data="idx_old")],
        [InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_back")]
    ])

def get_settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Powered By Text", callback_data="set_powered"), InlineKeyboardButton("🖼️ Set Bot DP & Bio", callback_data="set_bot_info")],
        [InlineKeyboardButton("✉️ Welcome & Reply Buttons", callback_data="set_welcome"), InlineKeyboardButton("❌ 'Not Found' Custom Link", callback_data="set_notfound")],
        [InlineKeyboardButton("🔍 Fuzzy Search Toggle", callback_data="toggle_fuzzy")],
        [InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_back")]
    ])

def get_movie_result_keyboard(files, page=1, total_pages=1):
    buttons = []
    for file in files:
        file_name = file.get("file_name", "Movie File")
        file_size = file.get("file_size", "")
        file_id = file.get("file_id", "")
        buttons.append([InlineKeyboardButton(f"📦 [ {file_size} ] 📂 {file_name}", url="https://t.me/your_channel")])

    buttons.append([
        InlineKeyboardButton("Send All", callback_data="send_all"),
        InlineKeyboardButton("Languages", callback_data="filter_lang"),
        InlineKeyboardButton("Years", callback_data="filter_years")
    ])
    buttons.append([
        InlineKeyboardButton("Quality", callback_data="filter_quality"),
        InlineKeyboardButton("Episodes", callback_data="filter_episodes"),
        InlineKeyboardButton("Seasons", callback_data="filter_seasons")
    ])
    buttons.append([
        InlineKeyboardButton("PAGE", callback_data="noop"),
        InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"),
        InlineKeyboardButton("NEXT ➔", callback_data="next_page")
    ])
    return InlineKeyboardMarkup(buttons)
