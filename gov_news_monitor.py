"""
India + Rajasthan Government News Monitor
Polls configured RSS feeds every 120 seconds and forwards only new items
whose final source URL is on an official Indian government domain.
"""
import asyncio
import html
import logging
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import feedparser
from telegram import Bot

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gov-news-monitor")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_IDS = [x.strip() for x in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]
POLL_SECONDS = max(120, int(os.getenv("POLL_SECONDS", "120")))
DB_PATH = os.getenv("NEWS_DB_PATH", "gov_news.sqlite3")

# Add or replace feeds in Render's FEED_URLS environment variable (comma-separated).
# These Google News RSS queries are discovery feeds; a story is sent only after
# its resolved URL passes the official-domain allowlist below.
DEFAULT_FEEDS = [
    "https://news.google.com/rss/search?q=site%3Agov.in+OR+site%3Anic.in+India+government+notification+OR+press+release&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=site%3Apib.gov.in+PIB+press+release&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=site%3Arajasthan.gov.in+OR+site%3Arajasthan.nic.in+Rajasthan+government+notification+OR+order&hl=hi&gl=IN&ceid=IN:hi",
    "https://news.google.com/rss/search?q=site%3Adipr.rajasthan.gov.in+Rajasthan+government+news&hl=hi&gl=IN&ceid=IN:hi",
]
FEEDS = [x.strip() for x in os.getenv("FEED_URLS", "").split(",") if x.strip()] or DEFAULT_FEEDS

# Runtime health details shown by /status and /lastcheck.
HEALTH = {"last_check": None, "last_delivery": None, "last_error": None,
          "feeds_checked": 0, "items_sent": 0}

OFFICIAL_SUFFIXES = (
    ".gov.in", ".nic.in", ".rajasthan.gov.in", ".rajasthan.nic.in",
    ".pib.gov.in", ".india.gov.in",
)

def official_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        return host.endswith(OFFICIAL_SUFFIXES) or host in {
            "gov.in", "nic.in", "pib.gov.in", "india.gov.in"
        }
    except Exception:
        return False

def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS sent_items (
            item_id TEXT PRIMARY KEY,
            sent_at TEXT NOT NULL
        )""")

def was_sent(item_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as db:
        return db.execute("SELECT 1 FROM sent_items WHERE item_id=?", (item_id,)).fetchone() is not None

def mark_sent(item_id: str):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT OR IGNORE INTO sent_items(item_id, sent_at) VALUES (?, ?)",
                   (item_id, datetime.now(timezone.utc).isoformat()))

def resolve_entry_url(entry) -> str:
    # feedparser may expose a Google News redirect URL; follow it to its final destination.
    import requests
    url = entry.get("link", "")
    if not url:
        return ""
    try:
        response = requests.get(url, timeout=15, allow_redirects=True,
                                headers={"User-Agent": "GovNewsMonitor/1.0"})
        return response.url
    except Exception:
        return url

def publication_time(entry) -> str:
    raw = entry.get("published") or entry.get("updated")
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            return dt.astimezone().strftime("%d-%m-%Y %I:%M %p %Z")
        except Exception:
            pass
    return "प्रकाशन समय उपलब्ध नहीं"

def stable_id(entry, final_url: str) -> str:
    return str(entry.get("id") or entry.get("guid") or final_url)

def parse_feed(url: str):
    return feedparser.parse(url)

async def scan_once(bot: Bot):
    HEALTH["last_check"] = datetime.now().astimezone().strftime("%d-%m-%Y %I:%M:%S %p %Z")
    HEALTH["feeds_checked"] = 0
    for feed_url in FEEDS:
        try:
            parsed = await asyncio.to_thread(parse_feed, feed_url)
            HEALTH["feeds_checked"] += 1
            for entry in parsed.entries[:50]:
                title = (entry.get("title") or "शीर्षक उपलब्ध नहीं").strip()
                final_url = await asyncio.to_thread(resolve_entry_url, entry)
                if not official_url(final_url):
                    continue
                item_id = stable_id(entry, final_url)
                if not item_id or was_sent(item_id):
                    continue
                summary = (entry.get("summary") or entry.get("description") or "").strip()
                # Remove markup and keep Telegram message within safe limits.
                from html.parser import HTMLParser
                class StripHTML(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.parts = []
                    def handle_data(self, data):
                        self.parts.append(data)
                stripper = StripHTML()
                stripper.feed(summary)
                summary = " ".join(" ".join(stripper.parts).split())[:900]
                text = (
                    "🆕 <b>नई सरकारी सूचना</b>\n"
                    f"<b>{html.escape(title[:350])}</b>\n\n"
                    f"🏛 स्रोत: <code>{html.escape(urlparse(final_url).hostname or '')}</code>\n"
                    f"🕒 समय: {html.escape(publication_time(entry))}\n"
                )
                if summary:
                    text += f"\n{html.escape(summary)}\n"
                text += f"\n🔗 <a href=\"{html.escape(final_url, quote=True)}\">आधिकारिक सूचना देखें</a>"
                delivered = False
                for chat_id in CHAT_IDS:
                    try:
                        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML",
                                               disable_web_page_preview=True)
                        delivered = True
                        HEALTH["last_delivery"] = datetime.now().astimezone().strftime("%d-%m-%Y %I:%M:%S %p %Z")
                        HEALTH["items_sent"] += 1
                    except Exception:
                        log.exception("Telegram delivery failed for destination %s", chat_id)
                if delivered:
                    mark_sent(item_id)
        except Exception:
            HEALTH["last_error"] = feed_url
            log.exception("Feed scan failed: %s", feed_url)

async def monitor_loop(bot: Bot):
    while True:
        await scan_once(bot)
        await asyncio.sleep(POLL_SECONDS)

async def start_command(update, context):
    await update.effective_message.reply_text(
        "नमस्कार! यह India + Rajasthan Government News Monitor Bot है।\\n\\n"
        "यह सरकारी वेबसाइटों से नई सूचनाएँ खोजकर निर्धारित Telegram चैनल पर भेजता है।\\n"
        "अपनी स्थिति देखने के लिए /status भेजें।\\n"
        "अंतिम जाँच के लिए /lastcheck भेजें।\\n"
        "कमांड सूची के लिए /help भेजें।"
    )

async def status_command(update, context):
    last_check = HEALTH["last_check"] or "अभी तक कोई जाँच दर्ज नहीं"
    last_delivery = HEALTH["last_delivery"] or "अभी तक कोई News deliver नहीं हुई"
    state = "🟢 Bot process चालू है"
    destination = ", ".join(CHAT_IDS) if CHAT_IDS else "सेट नहीं है"
    await update.effective_message.reply_text(
        f"{state}\\n"
        f"📡 News feeds: {len(FEEDS)} configured\\n"
        f"🔎 पिछली जाँच: {last_check}\\n"
        f"📬 पिछली सफल News delivery: {last_delivery}\\n"
        f"📊 इस रनटाइम में भेजी गई News: {HEALTH['items_sent']}\\n"
        f"📡 इस चक्र में जाँची गई feeds: {HEALTH['feeds_checked']}\\n"
        f"📢 Destination: {destination}\\n"
        f"⚠️ पिछली feed error: {HEALTH['last_error'] or 'कोई दर्ज नहीं'}"
    )

async def lastcheck_command(update, context):
    await update.effective_message.reply_text(
        f"🔎 पिछली News जाँच: {HEALTH['last_check'] or 'अभी तक नहीं हुई'}\\n"
        f"📬 पिछली सफल delivery: {HEALTH['last_delivery'] or 'अभी तक नहीं हुई'}"
    )

async def help_command(update, context):
    await update.effective_message.reply_text(
        "/start – बॉट का परिचय\\n"
        "/status – बॉट और News Monitoring की स्थिति\\n"
        "/lastcheck – पिछली जाँच और सफल delivery का समय\\n"
        "/help – कमांड सूची"
    )

async def post_init(application):
    init_db()
    if not CHAT_IDS:
        log.warning("TELEGRAM_CHAT_IDS is empty. Add destination chat IDs in Render environment.")
    application.bot_data["gov_news_task"] = asyncio.create_task(monitor_loop(application.bot))

async def post_shutdown(application):
    task = application.bot_data.get("gov_news_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

class HealthHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        if self.path in ("/", "/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            body = b"Government News Monitor is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        log.info("Health endpoint: " + format, *args)


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server listening on 0.0.0.0:%s", port)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    # Render Web Services require an HTTP listener on the assigned PORT.
    start_health_server()

    from telegram.ext import Application
    from telegram.ext import CommandHandler
    app = (Application.builder().token(BOT_TOKEN)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("lastcheck", lastcheck_command))
    app.add_handler(CommandHandler("help", help_command))
    # Python 3.14 no longer implicitly creates a current event loop in
    # the main thread. python-telegram-bot's run_polling() expects one.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    # Poll Telegram updates for both channel monitoring and user commands.
    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()
