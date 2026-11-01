# India + Rajasthan Government News Monitor

A Telegram worker that checks RSS discovery feeds every 2 minutes and forwards only items whose final resolved link belongs to an official Indian government domain.

## Deploy on Render

1. Create a Telegram bot with BotFather and copy its token.
2. In Render, create a **Background Worker** from this repository. Render can also use the included `render.yaml`.
3. Add environment variables:
   - `BOT_TOKEN`: token from BotFather
   - `TELEGRAM_CHAT_IDS`: leave blank until you are ready; later add a channel username such as `@your_channel` or numeric chat ID. For multiple destinations, separate with commas.
   - `POLL_SECONDS`: `120`
4. Add the bot as an administrator to your Telegram channel with permission to post messages.
5. Deploy. Add your destination later in Render → Environment and redeploy/restart the worker.

## What it does

- Checks configured RSS feeds every 120 seconds (minimum enforced in code).
- Uses Google News RSS as discovery, then follows each link and forwards only if the final hostname is an official government domain (`.gov.in`, `.nic.in`, approved Rajasthan domains, PIB, etc.).
- Stores sent item IDs in SQLite on Render's persistent disk to avoid repeat alerts.
- Sends headline, available publication time, summary, and the resolved official link.
- Supports additional RSS sources through comma-separated `FEED_URLS`.

## Important limitations

- This is frequent polling, not a guaranteed real-time feed. A department may publish without an RSS feed or may block indexing; two-minute checks cannot guarantee every department's information.
- Some Google News links may not resolve to an official page; those items are intentionally skipped.
- To expand coverage, add verified official RSS feeds or department notice-page feeds to `FEED_URLS`.
- Render Background Worker and persistent disk may require a paid plan. Check current Render pricing before deploying.
- Do not put bot tokens or credentials in source code or commit them to GitHub.
