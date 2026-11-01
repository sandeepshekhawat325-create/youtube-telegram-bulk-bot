# India + Rajasthan Government News Monitor

A Telegram news monitor that checks configured RSS discovery feeds every 2 minutes and forwards new items only when their resolved source URL belongs to an approved official Indian government domain.

## Render Free Web Service setup

1. In Render, create or use a **Web Service** for this repository. The included `render.yaml` is configured as a Free Web Service.
2. Build command: `pip install -r requirements.txt`
3. Start command: `python gov_news_monitor.py`
4. Health check path: `/health`
5. Set environment variables:
   - `BOT_TOKEN`: your current Telegram bot token. Keep it secret; never paste it into logs or GitHub.
   - `TELEGRAM_CHAT_IDS`: `@currentaffair_news` (the bot must be a channel administrator with permission to post).
   - `POLL_SECONDS`: `120`
   - `NEWS_DB_PATH`: `gov_news.sqlite3`
6. Deploy the latest commit and inspect Render Logs for feed fetches and Telegram delivery errors.

## What it does

- Checks configured feeds every 120 seconds while the process is running.
- Uses Google News RSS for discovery, then follows each link and forwards only if the final hostname passes the official-domain allowlist.
- Stores sent item IDs in SQLite to reduce duplicate posts.
- Sends headline, available publication time, summary, and the resolved official link.
- Supports additional feeds through comma-separated `FEED_URLS`.
- Provides Telegram commands: `/start`, `/status`, `/lastcheck`, and `/help`.

## Free-plan limitations (important)

- Render Free Web Services spin down after 15 minutes without inbound traffic and may restart. Therefore **Free hosting cannot guarantee continuous 24/7 monitoring**. A health check path helps Render verify service health; it does not guarantee that the service stays awake.
- The free filesystem is ephemeral. The SQLite database can be lost on restart/redeploy, which may cause previously sent stories to be considered new again.
- For reliable always-on monitoring and persistent deduplication, use a paid Render Background Worker with a Persistent Disk, or another always-on host plus persistent database.
- This is frequent polling, not guaranteed real-time or exhaustive coverage. Departments may not expose RSS feeds, and some links may fail to resolve to an official page.
- Never commit bot tokens or credentials to GitHub. If a token was exposed, revoke it with BotFather and update Render's `BOT_TOKEN`.
