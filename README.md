# Dahiya Movie Store Bot

Telegram movie/series search bot with MongoDB-backed file indexing, inline filters, an admin panel, broadcast tools, membership checks, and configurable auto-delete.

The provided video shows a Telegram inline-keyboard flow. This project uses the same kind of clickable Telegram buttons for language, year, quality, season, file selection, next/previous, and back navigation.

## Project files

The ZIP contains **18 files**:

- `bot/` — bot startup, Telegram handlers, MongoDB persistence, metadata parsing, old-file indexing, and inline keyboards.
- `tools/create_telethon_session.py` — creates the optional user session needed to read old channel history.
- `tests/test_metadata.py` — small metadata-parser checks.
- `requirements.txt`, `dev-requirements.txt` — runtime and test dependencies.
- `.env.example` — names of the environment variables; values are intentionally blank.
- `render.yaml`, `Procfile`, `runtime.txt` — Render setup and Python version.
- `.gitignore`, `README.md` — repository safety and this guide.

## What works

- `/start` and movie/series text search against the MongoDB index.
- Inline filters for language, year, quality, and season; file buttons, send-all (up to 10), and page navigation.
- Admin panel at `/admin`: bot on/off, live counts, member list with IDs and latest requests, source groups, old-file scan, settings, and broadcast.
- Bot profile image and description can be updated from Settings. The image is also used on the welcome message.
- Movie-not-found text can be changed or reset.
- Auto-delete can be switched on/off, set to 30 seconds, 2 minutes, 5 minutes, or a custom time. A countdown message refreshes while the timer runs. Pending deletion timers are stored in MongoDB.
- Force-subscription can be switched on/off; channels can be added and removed.
- New channel posts are indexed when new indexing is enabled.
- Old channel messages are scanned with a Telegram user session. Re-scanning updates the same channel/message records rather than creating duplicates.
- The movie index and settings live in MongoDB. Restarting or deploying new code does not clear indexed files.
- `/health` reports whether the bot service can reach MongoDB.

## Before deployment

You need:

1. A Telegram bot token from BotFather.
2. A MongoDB Atlas connection URI. The Atlas database user must have read/write access, and Atlas Network Access must permit connections from Render (commonly `0.0.0.0/0` with a strong database password).
3. Your numeric Telegram user ID for `ADMIN_USER_IDS`.
4. For old-file indexing only: Telegram API ID, API hash, and a Telethon session string. The session account must already be a member of each source channel/group.
5. Add the bot as an administrator in each source channel/group so it can see new channel posts and copy indexed files. The bot also needs admin rights in required subscription channels for membership checks.

Do not put real credentials in this folder, GitHub, the ZIP, or a chat message.

## Deploy from GitHub to Render

1. Extract the ZIP on your computer.
2. Create a new GitHub repository and upload the **contents of this folder** (the `bot/` directory should be at the repository root).
3. In Render, create a **Blueprint** from that repository and apply `render.yaml`, or create a Web Service with:
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `python -m bot.main`
   - Health-check path: `/health`
4. In Render's environment settings, add:
   - `BOT_TOKEN`
   - `MONGODB_URI`
   - `ADMIN_USER_IDS` — one or more numeric IDs separated by commas
   - `TELEGRAM_API_ID`
   - `TELEGRAM_API_HASH`
   - `TELETHON_SESSION` — optional for normal search/new indexing; required for old-file scans
   - `MONGODB_DATABASE` — optional; defaults to `dahiya_movie_store`
5. Deploy and wait for `/health` to return `{"status":"ok",...}`.
6. Open the bot, send `/start`, then `/admin`. Configure source channels and settings with the inline buttons.
7. To index old files, use Settings/Index old files after the user session is configured. The bot account and the Telethon user account both need access to the source.
8. For UptimeRobot, monitor `https://YOUR-RENDER-SERVICE.onrender.com/health`. This checks availability; it does not make a free Render service stay awake. For continuous 24/7 operation, use an always-on paid Render instance (the included Blueprint uses `starter`).

## Creating a Telethon session

The session string grants access to the Telegram account used to read old channel history. Keep it private.

```bash
python -m pip install -r requirements.txt
python tools/create_telethon_session.py
```

Enter your Telegram API ID and API hash when prompted, then complete the Telegram login. Copy the printed value directly to Render's `TELETHON_SESSION` secret field. Never commit the value or add it to `.env.example`.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Fill .env locally; keep it out of Git.
set -a && source .env && set +a
python -m bot.main
```

The service listens on `PORT` (default `10000`) and exposes `http://localhost:10000/health`.

## Admin panel actions

- `/admin` opens the control panel. Only IDs in `ADMIN_USER_IDS` can use it.
- **Settings** contains the profile image/description, auto-delete, timer, new-indexing, subscription, and not-found message controls.
- **Index groups** adds/removes sources. Removing a source does not delete its existing movie records.
- **Index old files** scans a registered source's message history.
- **Members** displays registered members, their Telegram ID, request count, and most recent request.
- **Broadcast** sends text and optionally adds a clickable URL button.

## Important limitations

- Historical scanning requires a valid Telethon session and account access to the channel. The bot token alone cannot read old channel history.
- New indexing requires the bot to be an administrator in each source channel. If Telegram denies access to the original message, the bot returns a button to open the source message instead.
- Search filters are parsed from filenames/captions. A language, year, quality, season, or episode filter will only be available when that information appears in the indexed message.
- A bot cannot guarantee a successful first deployment without valid secrets, MongoDB network access, and Telegram channel permissions. Check Render logs if Telegram or MongoDB rejects the configuration.
