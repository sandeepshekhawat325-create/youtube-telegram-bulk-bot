import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_DOWNLOAD_MB = int(os.getenv("MAX_DOWNLOAD_MB", "19"))
MAX_OUTPUT_MB = int(os.getenv("MAX_OUTPUT_MB", "49"))
WORK_DIR = Path(os.getenv("WORK_DIR", "/app/work"))
WORK_DIR.mkdir(parents=True, exist_ok=True)
