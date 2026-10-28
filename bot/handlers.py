import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from bot.keyboards import (
    get_admin_panel_keyboard,
    get_index_sources_keyboard,
    get_settings_keyboard,
    get_movie_result_keyboard
)

# Global Settings Dictionary
SETTINGS = {
    "powered_by": "Powered by : दईआमु restore",
    "fuzzy_search": True,
    "timer_enabled": True,
    "delete_timer": 30,
    "not_found_msg": "❌ Yeh movie hamare database mein nahi hai.",
    "not_found_link": "https://t.me/your_request_group"
}

@Client.on_message(filters.command("admin"))
async def admin_command(client: Client, message: Message):
    await message.reply(
        "👑 **DAHIYA MOVIE STORE ADMIN** 👑\n\nSelect an option below:",
        reply_markup=get_admin_panel_keyboard()
    )

@Client.on_callback_query(filters.regex("^admin_"))
async def admin_callback_handler(client: Client, callback: CallbackQuery):
    data = callback.data
    if data == "admin_dash":
        await callback.message.edit_text("📊 **Live Dashboard**\n\n🟢 Online Users: Active\n⚡ Live Requests: Monitoring ON", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]]))
    elif data == "admin_index":
        await callback.message.edit_text("📁 **Index Sources & Management**\n\nChoose an action:", reply_markup=get_index_sources_keyboard())
    elif data == "admin_settings":
        await callback.message.edit_text("⚙️ **Bot Settings & Customization**", reply_markup=get_settings_keyboard())
    elif data == "admin_back":
        await callback.message.edit_text("👑 **DAHIYA MOVIE STORE ADMIN** 👑", reply_markup=get_admin_panel_keyboard())
    elif data == "toggle_fuzzy":
        SETTINGS["fuzzy_search"] = not SETTINGS["fuzzy_search"]
        await callback.answer(f"Fuzzy Search is now {'ON' if SETTINGS['fuzzy_search'] else 'OFF'}", show_alert=True)

# Old Index button handler with Season String prompt
@Client.on_callback_query(filters.regex("^idx_old$"))
async def old_index_handler(client: Client, callback: CallbackQuery):
    await callback.message.reply(
        "📜 **Old Indexing Setup**\n\nKripya apne old files indexing ke liye **Season String** ya range bhejiye (jaise: `Season 1` ya `S01-S05`):"
    )
    await callback.answer()

@Client.on_message(filters.text & ~filters.command(["admin"]))
async def movie_search_handler(client: Client, message: Message):
    query = message.text.strip()
    user = message.from_user
    start_time = time.time()

    # Mock database search simulation (Fuzzy search enabled check)
    mock_files = []
    if query.lower() in ["kgf", "monarch", "raj"]:
        mock_files = [
            {"file_name": f"{query} Chapter 2 2022 WebRip 720p Hindi AAC", "file_size": "1.50 GB", "file_id": "1"},
            {"file_name": f"{query} Chapter 1 (2018) WebRip Hindi 720p", "file_size": "994.83 MB", "file_id": "2"}
        ]

    elapsed = round(time.time() - start_time, 2)

    # Agar movie database mein nahi milti (Customer Not Found with custom link)
    if not mock_files:
        not_found_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Custom Request Link", url=SETTINGS["not_found_link"])]
        ])
        await message.reply(f"{SETTINGS['not_found_msg']}\n\nMovie Searched: `{query}`", reply_markup=not_found_kb)
    return

    header_text = (
        f"✨ **THE RESULTS FOR** ✨\n"
        f"🏷️ `{query}`\n\n"
        f"👤 **Requested By :** {user.first_name}\n"
        f"⚡ **RESULT SHOW IN :** {elapsed} SECONDS\n"
        f"⭐ **{SETTINGS['powered_by']}**\n"
        f"🔍 Search Zone"
    )

    timer_text = ""
    if SETTINGS["timer_enabled"]:
        timer_text = f"\n\n⚠️ **AFTER {SETTINGS['delete_timer']} SECONDS THIS MESSAGE WILL BE AUTOMATICALLY DELETED** ⚠️"

    caption = header_text + timer_text + "\n\n📂 **Your Movie Files** 📂"
    keyboard = get_movie_result_keyboard(mock_files, page=1, total_pages=11)

    sent_msg = await message.reply(caption, reply_markup=keyboard)

    # Live auto delete countdown timer loop
    if SETTINGS["timer_enabled"]:
        for remaining in range(SETTINGS['delete_timer'], 0, -1):
            await asyncio.sleep(1)
        try:
            await sent_msg.delete()
        except Exception:
            pass
