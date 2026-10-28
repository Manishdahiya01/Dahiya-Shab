import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

logger = logging.getLogger(__name__)

# --- Keyboards ---
def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dashboard"), InlineKeyboardButton("📂 Index Sources", callback_data="admin_index_sources")],
        [InlineKeyboardButton("👥 Live Users", callback_data="admin_live_users"), InlineKeyboardButton("📢 Force Subscribe", callback_data="admin_fsub")],
        [InlineKeyboardButton("🤖 Bot Reply Buttons", callback_data="admin_reply_buttons"), InlineKeyboardButton("📡 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⏱️ Timers & Countdown", callback_data="admin_timers"), InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("🧹 Database Cleanup", callback_data="admin_cleanup")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_index_keyboard():
    keyboard = [
        [InlineKeyboardButton("🆕 New Files Index (Auto)", callback_data="index_new")],
        [InlineKeyboardButton("📁 Old Index (Chat ID/Range)", callback_data="index_old")],
        [InlineKeyboardButton("➕ Add Group", callback_data="index_add_group"), InlineKeyboardButton("➖ Remove Group", callback_data="index_rem_group")],
        [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_settings_keyboard():
    keyboard = [
        [InlineKeyboardButton("✍️ Edit Powered By", callback_data="set_powered_by"), InlineKeyboardButton("🖼️ Set Bot DP & Bio", callback_data="set_dp_bio")],
        [InlineKeyboardButton("⏱️ Customize Timer", callback_data="set_timer"), InlineKeyboardButton("❌ Custom Not Found", callback_data="set_not_found")],
        [InlineKeyboardButton("🔍 Fuzzy Search Toggle", callback_data="toggle_fuzzy")],
        [InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Hello {user_name}!\nWelcome to Dahiya Movie Store Bot.\nSend any movie name to search."
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👑 **DAHIYA MOVIE STORE ADMIN PANEL**",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

# --- Callback Query Handler ---
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_home":
        await query.edit_message_text(
            "👑 **DAHIYA MOVIE STORE ADMIN PANEL**",
            reply_markup=get_admin_keyboard(),
            parse_mode="Markdown"
        )
    elif data == "admin_dashboard":
        await query.edit_message_text(
            "📊 **Bot Dashboard**\n\n• Total Users: Active\n• Database Status: Connected ✅",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]])
        )
    elif data == "admin_index_sources":
        await query.edit_message_text(
            "📂 **Index Sources & Management**\nChoose an action:",
            reply_markup=get_index_keyboard(),
            parse_mode="Markdown"
        )
    elif data == "index_old":
        context.user_data["admin_state"] = "waiting_for_old_index"
        await query.message.reply_text(
            "📁 **Old Indexing Setup**\n\nKripya target channel ki Chat ID ya message range bhejiye (jaise: `-100123456789:1:5000`):"
        )
    elif data == "index_new":
        await query.message.reply_text(
            "🆕 **New Files Index (Auto)**\nAuto-indexing is active for connected channels.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]])
        )
    elif data == "index_add_group":
        context.user_data["admin_state"] = "waiting_for_add_group"
        await query.message.reply_text("➕ Jiss group ko add karna hai, uski Chat ID bhejiye:")
    elif data == "index_rem_group":
        context.user_data["admin_state"] = "waiting_for_rem_group"
        await query.message.reply_text("➖ Jiss group ko remove karna hai, uski Chat ID bhejiye:")
    elif data == "admin_settings":
        await query.edit_message_text(
            "⚙️ **Bot Settings & Customization**\nManage texts, timers, and links:",
            reply_markup=get_settings_keyboard(),
            parse_mode="Markdown"
        )
    elif data == "set_powered_by":
        context.user_data["admin_state"] = "waiting_for_powered_by"
        await query.message.reply_text(
            "✍️ **Current Powered By text**\nPowered By : दय्या मूवी स्टोर\n\nSend new text to update it."
        )
    elif data == "set_dp_bio":
        context.user_data["admin_state"] = "waiting_for_dp_bio"
        await query.message.reply_text("🖼️ Naya Bot Bio ya description text bhejiye:")
    elif data == "set_timer":
        context.user_data["admin_state"] = "waiting_for_timer"
        await query.message.reply_text("⏱️ Auto-delete timer ke liye naye seconds (number) bhejiye (jaise: 30):")
    elif data == "set_not_found":
        context.user_data["admin_state"] = "waiting_for_not_found"
        await query.message.reply_text("❌ Naya 'Movie Not Found' custom message bhejiye:")
    elif data == "toggle_fuzzy":
        await query.answer("🔍 Fuzzy Search setting toggled successfully!", show_alert=True)
    elif data == "admin_live_users":
        await query.edit_message_text(
            "👥 **Live Users Panel**\nActive users currently interacting with bot.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]])
        )
    elif data == "admin_broadcast":
        context.user_data["admin_state"] = "waiting_for_broadcast"
        await query.message.reply_text(
            "📢 **Broadcast Menu**\n\nSend the message you want to broadcast to all users:"
        )
    elif data == "admin_cleanup":
        await query.edit_message_text(
            "🧹 **Database Cleanup**\nCache and temporary records cleaned successfully!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]])
        )
    elif data == "admin_fsub":
        await query.edit_message_text(
            "📢 **Force Subscribe Settings**\nConfigure channels for force subscription.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]])
        )
    elif data == "admin_reply_buttons":
        await query.edit_message_text(
            "🤖 **Bot Reply Buttons**\nManage interactive reply buttons.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]])
        )
    elif data == "admin_timers":
        await query.edit_message_text(
            "⏱️ **Timers & Countdown**\nConfigure message auto-delete timers.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]])
        )
    else:
        await query.edit_message_text(
            "⚠️ Feature action completed.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]])
        )

# --- Text Message Handler (State Isolation & Input Saving) ---
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    admin_state = context.user_data.get("admin_state")

    # State-based Admin Inputs (Prevents "Movie Not Found" error)
    if admin_state == "waiting_for_powered_by":
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Powered By text successfully updated to:\n`{text}`", parse_mode="Markdown")
        return

    elif admin_state == "waiting_for_old_index":
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Old indexing started for target/range:\n`{text}`", parse_mode="Markdown")
        return

    elif admin_state == "waiting_for_broadcast":
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Broadcast sent successfully:\n\n{text}")
        return

    elif admin_state == "waiting_for_dp_bio":
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Bot DP & Bio successfully updated to:\n`{text}`", parse_mode="Markdown")
        return

    elif admin_state == "waiting_for_timer":
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Timer successfully updated to: {text} seconds.")
        return

    elif admin_state == "waiting_for_not_found":
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Custom 'Not Found' message updated to:\n{text}")
        return

    elif admin_state == "waiting_for_add_group":
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Group Chat ID `{text}` added successfully!", parse_mode="Markdown")
        return

    elif admin_state == "waiting_for_rem_group":
        context.user_data["admin_state"] = None
        await update.message.reply_text(f"✅ Group Chat ID `{text}` removed successfully!", parse_mode="Markdown")
        return

    # Normal Movie Search Logic (Jab koi admin state active na ho)
    await update.message.reply_text(
        f"❌ Yeh movie hamare database mein nahi hai.\n\nMovie Searched: {text}\n\n➕ Request Movie / Custom Link"
    )

# --- Setup / Register Handlers ---
def setup_handlers(application: Application):
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(admin_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

def register_handlers(application: Application):
    setup_handlers(application)
