import time
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from bot.database import db
from bot.keyboards import get_admin_panel_keyboard, get_index_sources_keyboard, get_settings_keyboard, get_movie_result_keyboard

# Global Dynamic Settings State
SETTINGS = {
    "powered_by": "Powered by : दईआमु restore",
    "fuzzy_search": True,
    "timer_enabled": True,
    "delete_timer": 30,
    "not_found_msg": "❌ Yeh movie hamare database mein nahi hai.",
    "not_found_link": "https://t.me/your_request_group",
    "force_sub_channel": None,
    "force_sub_enabled": False,
    "updates_link": "https://t.me/your_channel",
    "help_link": "https://t.me/your_support"
}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = (
        f"👋 **Hello {user.first_name}!\n\n"
        f"Welcome to Dahiya Movie Store Search Zone.**\n\n"
        f"Send any movie or web series name here or in groups to search instantly."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Search Movies", switch_inline_query_current_chat=""), InlineKeyboardButton("📢 Updates", url=SETTINGS["updates_link"])],
        [InlineKeyboardButton("✨ Help & Support", url=SETTINGS["help_link"])]
    ])
    await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode="Markdown")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text("👑 **DAHIYA MOVIE STORE ADMIN PANEL** 👑\n\nSelect an option to manage:", reply_markup=get_admin_panel_keyboard(), parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_dash":
        # Total indexed files count from MongoDB database
        try:
            total_files = await db.files.count_documents({})
        except Exception:
            total_files = 0
        await query.edit_message_text(f"📊 **Live Dashboard**\n\n🟢 Bot Status: Active & Online\n📁 Total Indexed Files: `{total_files}`\n⚡ Group Auto-Reply: Enabled", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]]))
    
    elif data == "admin_index":
        await query.edit_message_text("📁 **Index Sources & Management**\n\nChoose an action:", reply_markup=get_index_sources_keyboard(), parse_mode="Markdown")
    
    elif data == "idx_old":
        await query.message.reply_text("📜 **Old Indexing Setup**\n\nKripya target channel ki **Chat ID** ya message range bhejiye (jaise: `-100123456789:1:5000`):", parse_mode="Markdown")
    
    elif data == "admin_fsub":
        status = "🟢 ENABLED" if SETTINGS["force_sub_enabled"] else "🔴 DISABLED"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Toggle Force Sub: {status}", callback_data="toggle_fsub")],
            [InlineKeyboardButton("➕ Add/Change Channel", callback_data="set_fsub_channel")],
            [InlineKeyboardButton("⬅️ Back to Admin", callback_data="admin_back")]
        ])
        await query.edit_message_text("📢 **Force Subscribe Settings**\n\nConfigure mandatory channel subscription:", reply_markup=kb, parse_mode="Markdown")
    
    elif data == "toggle_fsub":
        SETTINGS["force_sub_enabled"] = not SETTINGS["force_sub_enabled"]
        await query.answer(f"Force Subscribe is now {'ON' if SETTINGS['force_sub_enabled'] else 'OFF'}", show_alert=True)
    
    elif data == "admin_settings":
        await query.edit_message_text("⚙️ **Bot Settings & Customization**\n\nManage texts, timers, and links:", reply_markup=get_settings_keyboard(), parse_mode="Markdown")
    
    elif data == "set_timer":
        await query.message.reply_text(f"⏱️ Current delete timer is **{SETTINGS['delete_timer']} seconds**. Send new seconds value as a number (e.g. `45`).", parse_mode="Markdown")
    
    elif data == "set_powered":
        await query.message.reply_text(f"✏️ Current Powered By text:\n`{SETTINGS['powered_by']}`\n\nSend new text to update it.", parse_mode="Markdown")

    elif data == "admin_back":
        await query.edit_message_text("👑 **DAHIYA MOVIE STORE ADMIN PANEL** 👑", reply_markup=get_admin_panel_keyboard(), parse_mode="Markdown")

async def movie_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.effective_message
    if not message or not message.text:
        return
    text = message.text.strip()
    if text.startswith("/"):
        return

    user = update.effective_user
    start_time = time.time()

    # Real MongoDB Search (Strict lookup, no fake mock results)
    try:
        files_cursor = db.files.find({"file_name": {"$regex": text, "$options": "i"}}).limit(10)
        found_files = await files_cursor.to_list(length=10)
    except Exception:
        found_files = []

    elapsed = round(time.time() - start_time, 3)

    # Agar database mein movie nahi milti toh 'Not Found' aur custom link dikhega
    if not found_files:
        not_found_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Request Movie / Custom Link", url=SETTINGS["not_found_link"])]
        ])
        await message.reply_text(
            f"{SETTINGS['not_found_msg']}\n\nMovie Searched: `{text}`", 
            reply_markup=not_found_kb, 
            parse_mode="Markdown"
        )
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

    reply_markup = get_movie_result_keyboard(found_files, page=1, total_pages=1)
    sent_msg = await message.reply_text(caption, reply_markup=reply_markup, parse_mode="Markdown")

    if SETTINGS["timer_enabled"]:
        await asyncio.sleep(SETTINGS["delete_timer"])
        try:
            await sent_msg.delete()
        except Exception:
            pass

def register_handlers(application):
    from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, movie_search_handler))
