import asyncio
import logging
import os
import tempfile
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import BOT_TOKEN, MAX_DOWNLOAD_MB, MAX_OUTPUT_MB, WORK_DIR
from editor import render_video

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("ai-video-editor")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🎬 AI Auto Video Editor\n\n"
        "मुझे एक वीडियो भेजें। Phase 1 में वीडियो MP4 में प्रोसेस होगी, "
        "ऑडियो normalize होगा और हल्का fade-in/fade-out लगेगा।\n"
        f"अधिकतम डाउनलोड सीमा: {MAX_DOWNLOAD_MB} MB"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "वीडियो सीधे भेजें। अभी यह Phase 1 MVP है। AI scene selection, "
        "Whisper subtitles और advanced effects अगले चरणों में जुड़ेंगे।"
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    media = message.video or message.document
    if not media:
        return

    file_size = getattr(media, "file_size", None)
    if file_size and file_size > MAX_DOWNLOAD_MB * 1024 * 1024:
        await message.reply_text(f"कृपया {MAX_DOWNLOAD_MB} MB से छोटी वीडियो भेजें।")
        return

    mime_type = getattr(media, "mime_type", None)
    if message.document and mime_type and not mime_type.startswith("video/"):
        await message.reply_text("कृपया video फ़ाइल भेजें।")
        return

    status = await message.reply_text("📥 वीडियो मिली। डाउनलोड और प्रोसेसिंग शुरू…")
    job_dir = Path(tempfile.mkdtemp(prefix="job_", dir=WORK_DIR))
    input_path = job_dir / "input_video"
    output_path = job_dir / "edited_video.mp4"

    try:
        telegram_file = await context.bot.get_file(media.file_id)
        await telegram_file.download_to_drive(custom_path=str(input_path))
        await status.edit_text("🎞️ वीडियो प्रोसेस हो रही है…")
        await asyncio.to_thread(render_video, input_path, output_path)

        if output_path.stat().st_size > MAX_OUTPUT_MB * 1024 * 1024:
            await status.edit_text(f"आउटपुट {MAX_OUTPUT_MB} MB से बड़ी है। छोटी वीडियो भेजें।")
            return

        await status.edit_text("✅ एडिटिंग पूरी! वीडियो भेज रहा हूँ…")
        with output_path.open("rb") as video_file:
            await message.reply_video(
                video=video_file,
                supports_streaming=True,
                caption="🎬 आपकी प्रोसेस की गई वीडियो तैयार है।",
                read_timeout=300,
                write_timeout=300,
                connect_timeout=60,
            )
        await status.delete()
    except Exception as exc:
        log.exception("Video processing failed")
        await status.edit_text(f"वीडियो प्रोसेस नहीं हो पाई। MP4 भेजकर फिर प्रयास करें।\nकारण: {str(exc)[:250]}")
    finally:
        for item in job_dir.glob("*"):
            try:
                item.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not remove temporary file: %s", item)
        try:
            job_dir.rmdir()
        except OSError:
            pass


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN .env में सेट करें।")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
