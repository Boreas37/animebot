import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")

CACHE_DIR: str = os.environ.get("CACHE_DIR", str(Path.home() / "anime_cache"))

MAX_CACHE_BYTES: int = int(
    os.environ.get("MAX_CACHE_BYTES", str(5 * 1024**3))
)

MAX_CONCURRENT_DOWNLOADS: int = int(
    os.environ.get("MAX_CONCURRENT_DOWNLOADS", "3")
)

MAX_TELEGRAM_FILE_SIZE: int = int(
    os.environ.get("MAX_TELEGRAM_FILE_SIZE", str(50 * 1024**2))
)

LOCAL_BOT_API_URL: str = os.environ.get("LOCAL_BOT_API_URL", "")

ADMIN_IDS: set[int] = {
    int(v) for v in os.environ.get("ADMIN_IDS", "").split(",") if v.strip()
}
