import os
import re
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
import yt_dlp

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
log = logging.getLogger("yt-telegram-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "45"))
MAX_VIDEOS_PER_JOB = int(os.getenv("MAX_VIDEOS_PER_JOB", "100"))
MAX_PLAYLIST_ITEMS = int(os.getenv("MAX_PLAYLIST_ITEMS", "500"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/tmp/yt-downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# This bot intentionally does not use browser cookies, login sessions, DRM bypass,
# or private-video access. Use only videos you own or are authorized to download.
URL_RE = re.compile(r"https?://\S+", re.I)


def is_admin(update: Update) -> bool:
    if not ADMIN_USER_ID:
        return False
    user = update.effective_user
    return bool(user and str(user.id) == ADMIN_USER_ID)


def clean_url(text: str) -> Optional[str]:
    match = URL_RE.search(text or "")
    if not match:
        return None
    url = match.group(0).rstrip(".,)>]}")
    if "youtube.com/" not in url.lower() and "youtu.be/" not in url.lower():
        return None
    return url


def ydl_base_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "noplaylist": False,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": MAX_PLAYLIST_ITEMS,
    }


def get_channel_entries(url: str):
    opts = ydl_base_opts()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return None, []
    entries = info.get("entries")
    if entries is None:
        # If a single video URL was supplied, treat it as a one-video job.
        entries = [info]
    cleaned = []
    for entry in entries:
        if not entry:
            continue
        vid = entry.get("id")
        webpage = entry.get("webpage_url") or entry.get("url")
        if not webpage and vid:
            webpage = f"https://www.youtube.com/watch?v={vid}"
        if webpage and "youtube.com" not in webpage and "youtu.be" not in webpage:
            if vid:
                webpage = f"https://www.youtube.com/watch?v={vid}"
        if webpage:
            cleaned.append({
                "id": vid or "",
                "title": entry.get("title") or "Untitled video",
                "url": webpage,
            })
    return info, cleaned


def download_one(url: str, workdir: str):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "best[filesize<={}] / best".format(MAX_UPLOAD_MB * 1024 * 1024),
        "outtmpl": os.path.join(workdir, "%(title).100s-%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "restrictfilenames": True,
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            return None, None
        path = Path(ydl.prepare_filename(info))
        if not path.exists():
            # yt-dlp may merge to mp4 after the original filename is prepared.
            mp4_path = path.with_suffix(".mp4")
            if mp4_path.exists():
                path = mp4_path
        return path, info.get("title") or path.stem


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("यह निजी बॉट है। अनुमति प्राप्त Telegram खाते से उपयोग करें।")
        return
    await update.message.reply_text(
        "नमस्ते! YouTube चैनल के Videos टैब का लिंक भेजें (जैसे https://www.youtube.com/@Channel/videos)।\n\n"
        "मैं वीडियो सूची दिखाऊँगा। फिर Download All दबाने पर अधिकतम "
        f"{MAX_VIDEOS_PER_JOB} वीडियो क्रम से भेजे जाएँगे।\n\n"
        "केवल अपने या डाउनलोड की अनुमति वाले वीडियो इस्तेमाल करें। नाम के बजाय चैनल URL भेजना सबसे विश्वसनीय है।\n"
        "/help — मदद"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "उपयोग:\n"
        "1) YouTube चैनल का Videos टैब लिंक भेजें: https://www.youtube.com/@handle/videos\n"
        "2) वीडियो सूची आने पर Download All दबाएँ।\n"
        "3) चाहें तो एक वीडियो का लिंक भी भेज सकते हैं।\n\n"
        "सीमाएँ: एक जॉब में अधिकतम {} वीडियो; Telegram पर भेजने की अधिकतम सेटिंग {} MB है। "
        "इससे बड़े वीडियो छोड़े जाएँगे। Render की अस्थायी डिस्क पर फाइलें स्थायी नहीं रहतीं।".format(
            MAX_VIDEOS_PER_JOB, MAX_UPLOAD_MB
        )
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("यह निजी बॉट है।")
        return
    url = clean_url(update.message.text)
    if not url:
        await update.message.reply_text(
            "कृपया YouTube चैनल का पूरा URL भेजें, बेहतर होगा Videos टैब का लिंक: "
            "https://www.youtube.com/@Channel/videos"
        )
        return

    msg = await update.message.reply_text("चैनल/प्लेलिस्ट की वीडियो सूची प्राप्त कर रहा हूँ…")
    try:
        info, entries = await asyncio.to_thread(get_channel_entries, url)
    except Exception as exc:
        log.exception("Could not inspect URL")
        await msg.edit_text(
            "वीडियो सूची नहीं मिल सकी। URL जाँचें और सार्वजनिक/सुलभ चैनल का Videos लिंक भेजें.\n"
            f"विवरण: {str(exc)[:300]}"
        )
        return

    if not entries:
        await msg.edit_text("इस लिंक से वीडियो नहीं मिले। चैनल का /videos लिंक भेजकर कोशिश करें।")
        return

    context.user_data["entries"] = entries[:MAX_VIDEOS_PER_JOB]
    context.user_data["source_url"] = url
    lines = []
    for i, item in enumerate(context.user_data["entries"][:15], 1):
        lines.append(f"{i}. {item['title'][:75]}")
    if len(entries) > len(lines):
        lines.append(f"… और {len(entries) - len(lines)} वीडियो")
    keyboard = [[InlineKeyboardButton(
        f"⬇️ Download All ({len(context.user_data['entries'])})",
        callback_data="download_all"
    )]]
    await msg.edit_text(
        f"वीडियो मिले: {len(entries)}\nइस जॉब में शामिल: {len(context.user_data['entries'])}\n\n"
        + "\n".join(lines)
        + "\n\nDownload All दबाकर अधिकृत वीडियो भेजें।",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def download_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update):
        await query.edit_message_text("यह निजी बॉट है।")
        return
    entries = context.user_data.get("entries") or []
    if not entries:
        await query.edit_message_text("वीडियो सूची समाप्त हो गई। चैनल लिंक फिर से भेजें।")
        return

    await query.edit_message_text(f"डाउनलोड शुरू… कुल वीडियो: {len(entries)}")
    chat_id = update.effective_chat.id
    succeeded, failed, too_large = 0, 0, 0

    for index, item in enumerate(entries, 1):
        status = await context.bot.send_message(
            chat_id=chat_id,
            text=f"({index}/{len(entries)}) तैयार कर रहा हूँ: {item['title'][:180]}"
        )
        try:
            with tempfile.TemporaryDirectory(dir=str(DOWNLOAD_DIR)) as workdir:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                path, title = await asyncio.to_thread(download_one, item["url"], workdir)
                if not path or not path.exists():
                    failed += 1
                    await status.edit_text(f"({index}/{len(entries)}) डाउनलोड नहीं हो सका: {item['title'][:150]}")
                    continue
                size_mb = path.stat().st_size / (1024 * 1024)
                if size_mb > MAX_UPLOAD_MB:
                    too_large += 1
                    await status.edit_text(
                        f"({index}/{len(entries)}) छोड़ा गया: फाइल {size_mb:.1f} MB है; "
                        f"सीमा {MAX_UPLOAD_MB} MB है।"
                    )
                    continue
                with path.open("rb") as video:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=video,
                        caption=f"{title[:900]}\nSource: {item['url']}",
                        supports_streaming=True,
                        read_timeout=120,
                        write_timeout=120,
                        connect_timeout=30,
                        pool_timeout=30,
                    )
                succeeded += 1
                await status.edit_text(f"({index}/{len(entries)}) भेज दिया ✅")
        except Exception as exc:
            failed += 1
            log.exception("Video job failed: %s", item.get("url"))
            try:
                await status.edit_text(f"({index}/{len(entries)}) असफल: {str(exc)[:180]}")
            except Exception:
                pass

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "जॉब पूरा हुआ।\n"
            f"✅ भेजे गए: {succeeded}\n"
            f"❌ असफल: {failed}\n"
            f"⚠️ आकार सीमा के कारण छोड़े: {too_large}\n\n"
            "ध्यान दें: चैनल के सभी वीडियो एक साथ भेजने में समय लग सकता है।"
        ),
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    if not ADMIN_USER_ID:
        raise RuntimeError("ADMIN_USER_ID environment variable is required")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(download_all_callback, pattern="^download_all$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Bot starting")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
