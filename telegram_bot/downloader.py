import asyncio
import os
import tempfile
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import yt_dlp

MIN_VIDEO_BYTES = 1 * 1024 * 1024
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "animebot_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


class _VideoTooSmall(Exception):
    pass


def _ytdlp_opts(url: str, output_path: str, progress_hooks: list | None = None) -> dict:
    opts: dict[str, Any] = {
        "outtmpl": output_path,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": "only_download",
        "retries": 5,
        "fragment_retries": 10,
        "nocheckcertificate": True,
        "concurrent_fragment_downloads": 5,
    }
    if progress_hooks:
        opts["progress_hooks"] = progress_hooks

    url_lower = url.lower()
    if "sibnet" in url_lower:
        opts["http_headers"] = {"Referer": "https://video.sibnet.ru/"}

    return opts


async def download_video(
    url: str,
    quality: str = "best",
    progress_callback: Callable[[dict], Coroutine] | None = None,
) -> str:
    loop = asyncio.get_running_loop()
    stem = uuid.uuid4().hex
    output_template = str(DOWNLOAD_DIR / f"{stem}.%(ext)s")

    progress_hooks = None
    if progress_callback is not None:
        def _progress_hook(d: dict) -> None:
            status = d.get("status", "")
            if status not in ("downloading", "finished"):
                return
            info = {
                "status": status,
                "downloaded_bytes": d.get("downloaded_bytes", 0),
                "total_bytes": d.get("total_bytes") or d.get("total_bytes_estimate", 0),
                "speed": d.get("speed"),
                "eta": d.get("eta"),
                "percent": d.get("_percent_str", "").strip().rstrip("%"),
            }
            asyncio.run_coroutine_threadsafe(progress_callback(info), loop)

        progress_hooks = [_progress_hook]

    opts = _ytdlp_opts(url, output_template, progress_hooks)
    if quality != "best":
        opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"
    else:
        opts["format"] = "bestvideo+bestaudio/best"

    def _run():
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception:
            raise

        final = None
        for p in os.listdir(DOWNLOAD_DIR):
            if p.startswith(stem):
                final = str(DOWNLOAD_DIR / p)
                break
        if final is None:
            raise FileNotFoundError(f"yt-dlp did not produce output for {stem}")

        size = os.path.getsize(final)
        if size < MIN_VIDEO_BYTES:
            os.remove(final)
            raise _VideoTooSmall(
                f"Downloaded file too small ({size} bytes), likely a broken source"
            )
        return final

    return await loop.run_in_executor(None, _run)
