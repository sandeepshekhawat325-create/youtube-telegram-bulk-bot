"""
India + Rajasthan Government News Monitor
Polls configured RSS feeds every 120 seconds and forwards only new items
whose final source URL is on an official Indian government domain.
"""
import asyncio
import html
import io
import re
import logging
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urljoin

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
MAX_NEWS_AGE_HOURS = max(1, int(os.getenv("MAX_NEWS_AGE_HOURS", "24")) )
INDIA_TZ = ZoneInfo("Asia/Kolkata")

# Add or replace feeds in Render's FEED_URLS environment variable (comma-separated).
# These Google News RSS queries are discovery feeds; a story is sent only after
# its resolved URL passes the official-domain allowlist below.
DEFAULT_FEEDS = [
    "https://news.google.com/rss/search?q=site%3Agov.in+OR+site%3Anic.in+central+government+notification+OR+circular+OR+order&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=site%3Apib.gov.in+press+release+India&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=site%3Arajasthan.gov.in+OR+site%3Arajasthan.nic.in+Rajasthan+government+notification+OR+order+OR+circular&hl=hi&gl=IN&ceid=IN:hi",
    "https://news.google.com/rss/search?q=site%3Adipr.rajasthan.gov.in+Rajasthan+press+release&hl=hi&gl=IN&ceid=IN:hi",
    "https://news.google.com/rss/search?q=site%3Arpsc.rajasthan.gov.in+RPSC+recruitment+result+exam+notification&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=site%3Arssb.rajasthan.gov.in+RSSB+recruitment+result+exam+notification&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=site%3Arajasthan.gov.in+Rajasthan+recruitment+exam+result+scheme&hl=hi&gl=IN&ceid=IN:hi",
]
FEEDS = [x.strip() for x in os.getenv("FEED_URLS", "").split(",") if x.strip()] or DEFAULT_FEEDS

# Runtime health details shown by /status and /lastcheck.
HEALTH = {"last_check": None, "last_delivery": None, "last_error": None,
          "feeds_checked": 0, "items_sent": 0, "entries_seen": 0,
          "fresh_entries": 0, "link_failures": 0,
          "official_candidates": 0, "duplicate_items": 0,
          "delivery_success": 0, "delivery_failed": 0,
          "no_official_streak": 0,
          "diagnostic_alert_active": False,
          "last_scan_summary": "अभी कोई जाँच नहीं हुई"}

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
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=(10, 25), allow_redirects=True,
                                    headers=headers)
            return response.url
        except Exception as exc:
            log.warning("Google News link resolution attempt %d failed: %s", attempt + 1, exc)
            if attempt == 2:
                return url

def entry_datetime(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    raw = entry.get("published") or entry.get("updated")
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None

def publication_time(entry) -> str:
    dt = entry_datetime(entry)
    return dt.astimezone(INDIA_TZ).strftime("%d-%m-%Y %I:%M:%S %p IST") if dt else "प्रकाशन समय उपलब्ध नहीं"

def detailed_summary(entry) -> str:
    chunks = []
    for key in ("content", "summary_detail", "summary", "description"):
        value = entry.get(key)
        if isinstance(value, list):
            chunks.extend(part.get("value", "") for part in value if isinstance(part, dict))
        elif isinstance(value, dict):
            chunks.append(value.get("value", ""))
        elif isinstance(value, str):
            chunks.append(value)
    from html.parser import HTMLParser
    class StripHTML(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, data):
            self.parts.append(data)
    cleaned = []
    for chunk in chunks:
        parser = StripHTML()
        parser.feed(chunk)
        value = " ".join(" ".join(parser.parts).split())
        if value and value not in cleaned:
            cleaned.append(value)
    return " ".join(cleaned)[:2400]

def stable_id(entry, final_url: str) -> str:
    return str(entry.get("id") or entry.get("guid") or final_url)

def parse_feed(url: str):
    import requests
    response = requests.get(
        url, timeout=(10, 25),
        headers={"User-Agent": "Mozilla/5.0 (compatible; GovNewsMonitor/1.1)"}
    )
    response.raise_for_status()
    return feedparser.parse(response.content)


def find_official_documents(page_url: str):
    """Find up to three PDF/Word attachments linked from an official page."""
    import requests
    from html.parser import HTMLParser
    class LinkParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []
        def handle_starttag(self, tag, attrs):
            if tag.lower() == "a":
                href = dict(attrs).get("href", "")
                if href:
                    self.links.append(urljoin(page_url, href))
    found = []
    if urlparse(page_url).path.lower().endswith((".pdf", ".doc", ".docx")):
        found.append(page_url)
    try:
        res = requests.get(page_url, timeout=(10, 20), headers={"User-Agent": "Mozilla/5.0"})
        if res.ok and "text/html" in res.headers.get("Content-Type", "").lower():
            parser = LinkParser()
            parser.feed(res.text[:2000000])
            for link in parser.links:
                if official_url(link) and urlparse(link).path.lower().endswith((".pdf", ".doc", ".docx")):
                    found.append(link)
    except Exception:
        log.exception("Official page document discovery failed: %s", page_url)
    return list(dict.fromkeys(found))[:3]

async def send_official_documents(bot: Bot, chat_id: str, page_url: str, title: str):
    import requests
    links = await asyncio.to_thread(find_official_documents, page_url)
    sent = 0
    for link in links:
        try:
            res = await asyncio.to_thread(
                requests.get, link, timeout=(10, 30),
                headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True
            )
            res.raise_for_status()
            if not official_url(res.url):
                continue
            ctype = res.headers.get("Content-Type", "").lower()
            path = urlparse(res.url).path.lower()
            if not (path.endswith((".pdf", ".doc", ".docx")) or
                    any(kind in ctype for kind in ("application/pdf", "msword", "wordprocessingml"))):
                continue
            if len(res.content) > 18 * 1024 * 1024:
                log.warning("Skipping document over 18 MB: %s", res.url)
                continue
            filename = res.url.rsplit("/", 1)[-1].split("?")[0] or "official_document"
            if not any(filename.lower().endswith(ext) for ext in (".pdf", ".doc", ".docx")):
                filename += ".pdf" if "pdf" in ctype else ".doc"
            data = io.BytesIO(res.content)
            data.name = filename
            await bot.send_document(chat_id=chat_id, document=data,
                                     caption="📎 मूल सरकारी दस्तावेज़: " + title[:700])
            sent += 1
        except Exception:
            log.exception("Official document download/send failed: %s", link)
    return sent

async def scan_once(bot: Bot):
    HEALTH["last_check"] = datetime.now(INDIA_TZ).strftime("%d-%m-%Y %I:%M:%S %p IST")
    HEALTH["feeds_checked"] = 0
    HEALTH["entries_seen"] = 0
    HEALTH["official_candidates"] = 0
    HEALTH["duplicate_items"] = 0
    HEALTH["delivery_success"] = 0
    HEALTH["delivery_failed"] = 0
    HEALTH["fresh_entries"] = 0
    HEALTH["link_failures"] = 0
    total_entries = 0
    sent_this_cycle = 0
    log.info("Starting news scan: %d feeds; destinations=%d", len(FEEDS), len(CHAT_IDS))
    if not CHAT_IDS:
        log.error("No Telegram destination configured; set TELEGRAM_CHAT_IDS in Render.")
    for feed_url in FEEDS:
        try:
            parsed = await asyncio.to_thread(parse_feed, feed_url)
            HEALTH["feeds_checked"] += 1
            HEALTH["last_scan_summary"] = f"जाँच जारी: {HEALTH['feeds_checked']}/{len(FEEDS)} feeds"
            entries = parsed.entries[:50]
            total_entries += len(entries)
            HEALTH["entries_seen"] += len(entries)
            log.info("Feed fetched: %s | entries=%d | bozo=%s",
                     urlparse(feed_url).hostname, len(entries), getattr(parsed, "bozo", False))
            for entry in entries:
                title = (entry.get("title") or "शीर्षक उपलब्ध नहीं").strip()
                published_dt = entry_datetime(entry)
                if published_dt is None:
                    log.info("Skipping item with no verifiable publication date: %s", title[:180])
                    continue
                age = datetime.now(timezone.utc) - published_dt
                if age < timedelta(minutes=-10) or age > timedelta(hours=MAX_NEWS_AGE_HOURS):
                    log.info("Skipping stale/future item (%s): %s", publication_time(entry), title[:180])
                    continue

                HEALTH["fresh_entries"] += 1
                final_url = await asyncio.to_thread(resolve_entry_url, entry)

                # Google News RSS sometimes keeps its own redirect URL even after
                # a successful request. Fall back to the publisher URL advertised
                # in the RSS source metadata, but only if that host is official.
                if not official_url(final_url):
                    source_meta = entry.get("source") or {}
                    source_url = (source_meta.get("href") or "").strip()
                    if official_url(source_url):
                        log.info("Using verified official publisher URL from RSS metadata: %s",
                                 urlparse(source_url).hostname)
                        final_url = source_url

                if not official_url(final_url):
                    HEALTH["link_failures"] += 1
                    log.info("Skipping item: no official URL found | title=%s | resolved=%s | source=%s",
                             title[:180], final_url[:220],
                             (entry.get("source") or {}).get("href", "")[:220])
                    continue
                published_dt = entry_datetime(entry)
                if published_dt is None:
                    log.info("Skipping item with no verifiable publication date: %s", title[:180])
                    continue
                age = datetime.now(timezone.utc) - published_dt
                if age < timedelta(minutes=-10) or age > timedelta(hours=MAX_NEWS_AGE_HOURS):
                    log.info("Skipping stale/future item (%s): %s", publication_time(entry), title[:180])
                    continue

                HEALTH["official_candidates"] += 1
                item_id = stable_id(entry, final_url)
                if not item_id:
                    continue
                if was_sent(item_id):
                    HEALTH["duplicate_items"] += 1
                    log.info("Skipping duplicate item: %s", title[:180])
                    continue
                summary = detailed_summary(entry)
                text = (
                    "🆕 <b>नई सरकारी सूचना</b>\\n"
                    f"<b>{html.escape(title[:350])}</b>\\n\\n"
                    f"🏛 <b>विभाग/स्रोत:</b> <code>{html.escape(urlparse(final_url).hostname or '')}</code>\\n"
                    f"🕒 <b>प्रकाशन:</b> {html.escape(publication_time(entry))}\\n"
                    f"📌 <b>विस्तृत जानकारी:</b>\\n"
                )
                if summary:
                    text += f"{html.escape(summary)}\\n"
                else:
                    text += "आधिकारिक RSS में विस्तृत विवरण उपलब्ध नहीं है। कृपया मूल अधिसूचना खोलें।\\n"
                text += (
                    "\\n🎯 <b>महत्त्व:</b> पात्रता, निर्देश, तिथि और लाभ/प्रभाव की पुष्टि "
                    "मूल आधिकारिक दस्तावेज़ से करें।\\n"
                    f"🔗 <a href=\\\"{html.escape(final_url, quote=True)}\\\">आधिकारिक सूचना / दस्तावेज़ देखें</a>"
                )
                delivered = False
                for chat_id in CHAT_IDS:
                    try:
                        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML",
                                               disable_web_page_preview=True)
                        delivered = True
                        HEALTH["delivery_success"] += 1
                        docs_sent = await send_official_documents(bot, chat_id, final_url, title)
                        log.info("Official attachments sent=%d for: %s", docs_sent, title[:160])
                        HEALTH["last_delivery"] = datetime.now().astimezone().strftime("%d-%m-%Y %I:%M:%S %p %Z")
                        HEALTH["items_sent"] += 1
                    except Exception:
                        HEALTH["delivery_failed"] += 1
                        log.exception("Telegram delivery failed for destination %s", chat_id)
                if delivered:
                    mark_sent(item_id)
                    sent_this_cycle += 1
        except Exception:
            HEALTH["last_error"] = feed_url
            log.exception("Feed scan failed: %s", feed_url)
    HEALTH["last_scan_summary"] = (
        f"कुल आइटम {total_entries}, आधिकारिक लिंक {HEALTH['official_candidates']}, "
        f"भेजी गई खबरें {sent_this_cycle}, सफल feeds {HEALTH['feeds_checked']}/{len(FEEDS)}"
    )
    log.info("News scan complete: %s", HEALTH["last_scan_summary"])

    # Notify the owner/channel only when the same actionable problem persists.
    # Avoid false alarms when a scan simply contains no fresh RSS items.
    if HEALTH["fresh_entries"] > 0 and HEALTH["official_candidates"] == 0:
        HEALTH["no_official_streak"] += 1
    elif HEALTH["official_candidates"] > 0:
        HEALTH["no_official_streak"] = 0
        if HEALTH["diagnostic_alert_active"]:
            for chat_id in CHAT_IDS:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text="✅ <b>News Monitor Recovery</b>\\n"
                             "आधिकारिक सरकारी लिंक फिर से मिलने लगे हैं।\\n"
                             + html.escape(HEALTH["last_scan_summary"]),
                        parse_mode="HTML"
                    )
                except Exception:
                    log.exception("Recovery alert failed for %s", chat_id)
            HEALTH["diagnostic_alert_active"] = False

    if HEALTH["no_official_streak"] >= 3 and not HEALTH["diagnostic_alert_active"]:
        alert = (
            "⚠️ <b>News Monitor Diagnostic Alert</b>\\n"
            "लगातार 3 स्कैन में नई RSS खबरें मिलीं, लेकिन एक भी आधिकारिक सरकारी लिंक सत्यापित नहीं हुआ।\\n\\n"
            f"RSS आइटम: {total_entries}\\n"
            f"ताज़ा आइटम: {HEALTH['fresh_entries']}\\n"
            f"लिंक जाँच विफल: {HEALTH['link_failures']}\\n\\n"
            "संभावित कारण: Google News redirect URL resolve नहीं हो रहा या RSS publisher metadata में official URL नहीं है।\\n"
            "Bot और Telegram delivery चालू हैं; लिंक पहचानने वाले चरण की जाँच आवश्यक है।"
        )
        for chat_id in CHAT_IDS:
            try:
                await bot.send_message(chat_id=chat_id, text=alert, parse_mode="HTML")
            except Exception:
                log.exception("Diagnostic alert failed for %s", chat_id)
        HEALTH["diagnostic_alert_active"] = True

async def monitor_loop(bot: Bot):
    # Never let a transient feed/API/database error permanently stop monitoring.
    while True:
        started = datetime.now(timezone.utc)
        try:
            await scan_once(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Unexpected error in monitoring cycle; will retry")
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        delay = max(5, POLL_SECONDS - int(elapsed))
        log.info("Next news scan in %s seconds", delay)
        await asyncio.sleep(delay)

async def start_command(update, context):
    await update.effective_message.reply_text(
        "नमस्कार! यह India + Rajasthan Government News Monitor Bot है।\\n\\n"
        "यह सरकारी वेबसाइटों से नई सूचनाएँ खोजकर निर्धारित Telegram चैनल पर भेजता है।\\n"
        "अपनी स्थिति देखने के लिए /status भेजें।\\n"
        "अंतिम जाँच के लिए /lastcheck भेजें।\\n"
        "कमांड सूची के लिए /help भेजें।"
    )

async def status_command(update, context):
    last_check = HEALTH["last_check"] or "अभी तक दर्ज नहीं"
    last_delivery = HEALTH["last_delivery"] or "अभी तक कोई खबर नहीं भेजी गई"
    destination = ", ".join(CHAT_IDS) if CHAT_IDS else "सेट नहीं है"
    feeds_total = len(FEEDS)
    feeds_done = HEALTH["feeds_checked"]
    scan_summary = HEALTH["last_scan_summary"]

    status_text = (
        "🤖 GOVERNMENT NEWS MONITOR\\n"
        "━━━━━━━━━━━━━━━━━━━━\\n"
        "🟢 सिस्टम स्थिति: चालू है\\n"
        f"📡 RSS Feeds: {feeds_done}/{feeds_total} जाँची गई\\n"
        f"🕒 पिछली जाँच: {last_check}\\n"
        f"📬 पिछली सफल खबर: {last_delivery}\\n"
        "━━━━━━━━━━━━━━━━━━━━\\n"
        "📊 NEWS SCAN REPORT\\n"
        f"📰 {scan_summary}\\n"
        f"♻️ Duplicate खबरें: {HEALTH['duplicate_items']}\\n"
        f"📤 Telegram Delivery: {HEALTH['delivery_success']} सफल | "
        f"{HEALTH['delivery_failed']} असफल\\n"
        f"📈 इस रनटाइम में भेजी गई खबरें: {HEALTH['items_sent']}\\n"
        "━━━━━━━━━━━━━━━━━━━━\\n"
        "📢 CHANNEL DETAILS\\n"
        f"🎯 Destination: {destination}\\n"
        f"⚠️ Feed Error: {HEALTH['last_error'] or 'कोई नहीं'}"
    )
    await update.effective_message.reply_text(status_text)

async def lastcheck_command(update, context):
    await update.effective_message.reply_text(
        f"🔎 पिछली News जाँच: {HEALTH['last_check'] or 'अभी तक नहीं हुई'}\\n"
        f"📬 पिछली सफल delivery: {HEALTH['last_delivery'] or 'अभी तक नहीं हुई'}"
    )

async def test_command(update, context):
    if not CHAT_IDS:
        await update.effective_message.reply_text("❌ TELEGRAM_CHAT_IDS सेट नहीं है।")
        return
    sent = 0
    for chat_id in CHAT_IDS:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="✅ Government News Bot Test Message\\n"
                     "Telegram channel delivery सही है। यह केवल परीक्षण संदेश है।"
            )
            sent += 1
        except Exception:
            log.exception("Test message failed for destination %s", chat_id)
    await update.effective_message.reply_text(
        f"टेस्ट संदेश {sent}/{len(CHAT_IDS)} चैनल पर भेजा गया। "
        "यदि संख्या 0 है तो Bot Admin permission और TELEGRAM_CHAT_IDS जाँचें।"
    )

async def help_command(update, context):
    await update.effective_message.reply_text(
        "/start – बॉट का परिचय\\n"
        "/status – बॉट और News Monitoring की स्थिति\\n"
        "/lastcheck – पिछली जाँच और सफल delivery का समय\\n"
        "/test – चैनल पर परीक्षण संदेश भेजें\\n"
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
    app.add_handler(CommandHandler("test", test_command))
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
