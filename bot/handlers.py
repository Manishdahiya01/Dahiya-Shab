import time
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from bot.keyboards import get_admin_panel_keyboard, get_index_sources_keyboard, get_settings_keyboard, get_movie_result_keyboard

SETTINGS = {
    "powered_by": "Powered by : दईआमु restore",
    "fuzzy_search": True,
    "timer_enabled": True,
    "delete_timer": 30,
    "not_found_msg": "❌ Yeh movie hamare database mein nahi hai.",
    "not_found_link": "https://t.me/your_request_group"
}

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👑 **DAHIYA MOVIE STORE ADMIN** 👑\n\nSelect an option below:", reply_markup=get_admin_panel_keyboard(), parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_dash":
        await query.edit_message_text("📊 **Live Dashboard**\n\n🟢 Online Users: Active\n⚡ Live Requests: Monitoring ON", parse_mode="Markdown")
    elif data == "admin_index":
        await query.edit_message_text("📁 **Index Sources & Management**\n\nChoose an action:", reply_markup=get_index_sources_keyboard(), parse_mode="Markdown")
    elif data == "idx_old":
        await query.message.reply_text("📜 **Old Indexing Setup**\n\nKripya apne old files indexing ke liye **Season String** ya range bhejiye (jaise: `Season 1` ya `S01-S05`):", parse_mode="Markdown")
    elif data == "toggle_fuzzy":
        SETTINGS["fuzzy_search"] = not SETTINGS["fuzzy_search"]
        await query.answer(f"Fuzzy Search is now {'ON' if SETTINGS['fuzzy_search'] else 'OFF'}", show_alert=True)

async def movie_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.startswith("/"):
        return

    user = update.effective_user
    start_time = time.time()

    mock_files = [
        {"file_name": f"{text} Chapter 2 2022 WebRip 720p Hindi AAC", "file_size": "1.50 GB"},
        {"file_name": f"{text} Chapter 1 (2018) WebRip Hindi 720p", "file_size": "994.83 MB"}
    ]

    elapsed = round(time.time() - start_time, 2)

    if not mock_files:
        not_found_kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Custom Request Link", url=SETTINGS["not_found_link"])]])
        await update.message.reply_text(f"{SETTINGS['not_found_msg']}\n\nMovie Searched: `{text}`", reply_markup=not_found_kb, parse_mode="Markdown")
        return

    header_text = (
        f"✨ **THE RESULTS FOR** ✨\n"
        f"🏷️ `{text}`\n\n"
        f"👤 **Requested By :** {user.first_name}\n"
        f"⚡ **RESULT SHOW IN :** {elapsed} SECONDS\n"
        f"⭐ **{SETTINGS['powered_by']}**\n"
        f"🔍 Search Zone"
    )

    timer_text = f"\n\n⚠️ **AFTER {SETTINGS['delete_timer']} SECONDS THIS MESSAGE WILL BE AUTOMATICALLY DELETED** ⚠️" if SETTINGS["timer_enabled"] else ""
    caption = header_text + timer_text + "\n\n📂 **Your Movie Files** 📂"
    reply_markup = get_movie_result_keyboard(mock_files, page=1, total_pages=11)

    sent_msg = await update.message.reply_text(caption, reply_markup=reply_markup, parse_mode="Markdown")

    if SETTINGS["timer_enabled"]:
        await asyncio.sleep(SETTINGS["delete_timer"])
        try:
            await sent_msg.delete()
        except Exception:
            pass

def register_handlers(application):
    from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, movie_search_handler))
