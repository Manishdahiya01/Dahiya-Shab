from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def get_admin_panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dash"), InlineKeyboardButton("📁 Index Sources", callback_data="admin_index")],
        [InlineKeyboardButton("👥 Live Users", callback_data="admin_live"), InlineKeyboardButton("📢 Force Subscribe", callback_data="admin_fsub")],
        [InlineKeyboardButton("✉️ Bot Reply Buttons", callback_data="admin_reply_settings"), InlineKeyboardButton("📡 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⏱️ Timers & Countdown", callback_data="admin_timer"), InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("🧹 Database Cleanup", callback_data="admin_cleanup")]
    ])

def get_index_sources_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 New Files Index (Auto)", callback_data="idx_new")],
        [InlineKeyboardButton("📜 Old Index (Chat ID/Range)", callback_data="idx_old")],
        [InlineKeyboardButton("➕ Add Group", callback_data="idx_add"), InlineKeyboardButton("➖ Remove Group", callback_data="idx_rem")],
        [InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_back")]
    ])

def get_settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Powered By Text", callback_data="set_powered"), InlineKeyboardButton("🖼️ Set Bot DP & Bio", callback_data="set_bot_info")],
        [InlineKeyboardButton("⏱️ Customize Timer", callback_data="set_timer"), InlineKeyboardButton("❌ Custom Not Found Link", callback_data="set_notfound")],
        [InlineKeyboardButton("🔍 Fuzzy Search Toggle", callback_data="toggle_fuzzy")],
        [InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_back")]
    ])

def get_movie_result_keyboard(files, page=1, total_pages=1):
    buttons = []
    for file in files:
        f_name = file.get("file_name", "Movie File")
        f_size = file.get("file_size", "1.0 GB")
        f_id = str(file.get("_id", ""))
        buttons.append([InlineKeyboardButton(f"📦 [ {f_size} ] 📂 {f_name}", callback_data=f"get_file_{f_id}")])

    buttons.extend([
        [InlineKeyboardButton("Send All", callback_data="send_all"), InlineKeyboardButton("Languages", callback_data="filter_lang"), InlineKeyboardButton("Years", callback_data="filter_years")],
        [InlineKeyboardButton("Quality", callback_data="filter_quality"), InlineKeyboardButton("Episodes", callback_data="filter_episodes"), InlineKeyboardButton("Seasons", callback_data="filter_seasons")],
        [InlineKeyboardButton("PAGE", callback_data="noop"), InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"), InlineKeyboardButton("NEXT ➔", callback_data="next_page")]
    ])
    return InlineKeyboardMarkup(buttons)
