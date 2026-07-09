import asyncio
import logging
import os
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from cache_manager import CacheManager
from downloader import download_video
from scraper import get_anime_info, get_episodes, get_video_sources, resolve_video_url, search_anime
from subscription_manager import SubscriptionManager, FREE_DOWNLOADS, SEASON_SIZE, SUBSCRIPTION_PRICE_STARS, SUBSCRIPTION_DAYS
from telegram_cache import TelegramCache
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from preloader import Preloader
from db import create_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if config.LOCAL_BOT_API_URL:
    base_url = config.LOCAL_BOT_API_URL.rstrip("/")
    api_server = TelegramAPIServer(
        base=f"{base_url}/bot{{token}}/{{method}}",
        file=f"{base_url}/file/bot{{token}}/{{path}}",
        is_local=True,
    )
    session = AiohttpSession(api=api_server)
    bot = Bot(token=config.BOT_TOKEN, session=session)
else:
    bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

cache: CacheManager | None = None
sub_manager: SubscriptionManager | None = None
tg_cache: TelegramCache | None = None
preloader: Preloader | None = None
cache_group_id: int | None = None

from cachetools import TTLCache

_anime_cache: TTLCache = TTLCache(maxsize=5000, ttl=2 * 3600)
_episode_cache: TTLCache = TTLCache(maxsize=5000, ttl=2 * 3600)
_next_id: int = 0

EPISODES_PER_PAGE = 20

DOWNLOAD_TIMEOUT = 120

download_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)
_download_locks: dict[str, asyncio.Lock] = {}
_download_locks_lock = asyncio.Lock()


async def _get_download_lock(cache_key: str) -> asyncio.Lock:
    async with _download_locks_lock:
        if cache_key not in _download_locks:
            _download_locks[cache_key] = asyncio.Lock()
        return _download_locks[cache_key]


async def _release_download_lock(cache_key: str) -> None:
    async with _download_locks_lock:
        lock = _download_locks.get(cache_key)
        if lock is not None and not lock.locked():
            del _download_locks[cache_key]


def _make_episode_keyboard(ep_cache_id: int, episodes: list[dict[str, Any]], page: int) -> InlineKeyboardMarkup:
    total_pages = (len(episodes) + EPISODES_PER_PAGE - 1) // EPISODES_PER_PAGE
    start = page * EPISODES_PER_PAGE
    end = min(start + EPISODES_PER_PAGE, len(episodes))
    page_eps = episodes[start:end]

    builder = InlineKeyboardBuilder()
    for ep in page_eps:
        builder.row(
            InlineKeyboardButton(
                text=f"{ep['no']}. {ep['title']}",
                callback_data=f"e:{ep_cache_id}:{ep['no']}",
            )
        )

    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="◀ Önceki", callback_data=f"p:{ep_cache_id}:{page - 1}")
        )
    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="Sonraki ▶", callback_data=f"p:{ep_cache_id}:{page + 1}")
        )
    if nav_buttons:
        builder.row(*nav_buttons)

    if page == 0:
        builder.row(
            InlineKeyboardButton(text=f"📦 Tüm Sezonu İndir ({SEASON_SIZE} bölüm)", callback_data=f"s:{ep_cache_id}")
        )
    builder.row(InlineKeyboardButton(text="« Ana Menü", callback_data="start"))
    return builder.as_markup()


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    text = (
        "Merhaba! 👋\n\n"
        "Bu bot ile turkanime.net üzerinden anime bölümlerini arayabilir "
        "ve indirebilirsiniz.\n\n"
        f"İlk {FREE_DOWNLOADS} indirme ücretsiz! Devamı için aylık {SUBSCRIPTION_PRICE_STARS} ⭐.\n\n"
        "Komutlar:\n"
        "/ara <anime adı> — Anime araması yapar\n"
        "/abonelik — Abonelik durumunu gösterir\n"
        "/setcache — Bir grubu cache havuzu olarak ayarlar\n"
        "/preload — Ön yükleme durumunu gösterir\n"
        "/cachele <slug> — Ön yüklemeye anime ekler\n"
        "/stats — Cache istatistiklerini gösterir\n\n"
        "Örnek: /ara non non biyori"
    )
    await message.answer(text, reply_markup=_start_keyboard())


def _start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[])


# ---------------------------------------------------------------------------
# /ara
# ---------------------------------------------------------------------------

@router.message(Command("ara"))
async def cmd_ara(message: Message, command: CommandObject) -> None:
    query = command.args
    if not query:
        await message.answer("Lütfen bir anime adı girin. Örnek: /ara non non biyori")
        return

    msg = await message.answer(f"🔍 \"{query}\" aranıyor...")
    try:
        results = await asyncio.to_thread(search_anime, query)
    except Exception as e:
        logger.exception("Search failed")
        await msg.edit_text("Arama yapılırken bir hata oluştu. Lütfen tekrar deneyin.")
        return

    if not results:
        await msg.edit_text(f"\"{query}\" için sonuç bulunamadı.")
        return

    global _next_id

    builder = InlineKeyboardBuilder()
    for idx, r in enumerate(results):
        cid = _next_id
        _next_id += 1
        _anime_cache[cid] = r["slug"]
        builder.row(
            InlineKeyboardButton(
                text=r["title"],
                callback_data=f"a:{cid}",
            )
        )
    await msg.edit_text("Arama sonuçları:", reply_markup=builder.as_markup())


# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# /abonelik
# ---------------------------------------------------------------------------

@router.message(Command("abonelik"))
async def cmd_abonelik(message: Message) -> None:
    user_id = message.from_user.id
    text = await sub_manager.info(user_id)
    ok, reason = await sub_manager.can_download(user_id)
    if not ok:
        text += f"\n\n{reason}"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"💳 {SUBSCRIPTION_PRICE_STARS} ⭐ ile Abone Ol", callback_data="subscribe")]
            ]
        )
    else:
        kb = None
    await message.answer(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# /setcache — set the cache group
# ---------------------------------------------------------------------------

def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


@router.message(Command("setcache"))
async def cmd_setcache(message: Message) -> None:
    global cache_group_id
    if not _is_admin(message.from_user.id):
        await message.answer("Bu komutu kullanmaya yetkin yok.")
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Bu komutu yalnızca bir grupta kullanabilirsin.")
        return
    cache_group_id = message.chat.id
    await cache.set_config("cache_group_id", str(cache_group_id))
    await message.answer(f"✅ Cache grubu olarak \"{message.chat.title}\" ayarlandı. Artık ön yüklemeler buraya gelecek.")


# ---------------------------------------------------------------------------
# /preload
# ---------------------------------------------------------------------------

@router.message(Command("preload"))
async def cmd_preload(message: Message) -> None:
    text = await preloader.stats()
    if cache_group_id:
        text += f"\nCache grubu: ✅"
    else:
        text += f"\nCache grubu: ❌ (/setcache ile ayarla)"
    await message.answer(text)


@router.message(Command("cachele"))
async def cmd_cachele(message: Message, command: CommandObject) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Bu komutu kullanmaya yetkin yok.")
        return
    slug = command.args
    if not slug:
        await message.answer("Kullanım: /cachele <anime-slug>\nSlug'ı turkanime.net'teki URL'den al: turkanime.net/anime/naruto -> naruto")
        return
    slug = slug.strip().split("/")[-1].split("?")[0]
    if "/" in slug:
        slug = slug.rstrip("/").split("/")[-1]
    msg = await message.answer(f"⏳ {slug} ekleniyor...")
    result = await preloader.add_to_queue(slug)
    await msg.edit_text(f"✅ {result}")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    s = await cache.stats()
    tg_count = await tg_cache.count()
    text = (
        f"📊 **Cache İstatistikleri**\n"
        f"Disk: {s['entry_count']} bölüm ({s['usage_gb']} GB / {s['max_gb']} GB)\n"
        f"Telegram: {tg_count} bölüm"
    )
    await message.answer(text)


# ---------------------------------------------------------------------------
# callback: start (ana menü)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "start")
async def cb_start(callback: CallbackQuery) -> None:
    await callback.answer()
    await cmd_start(callback.message)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# callback: subscribe -> send invoice
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "subscribe")
async def cb_subscribe(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer_invoice(
        title="Aylık Abonelik",
        description=f"{SUBSCRIPTION_DAYS} gün sınırsız anime indirme",
        payload="monthly_sub",
        currency="XTR",
        prices=[LabeledPrice(label=f"{SUBSCRIPTION_DAYS} Günlük Abonelik", amount=SUBSCRIPTION_PRICE_STARS)],
    )


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def on_payment(message: Message) -> None:
    user_id = message.from_user.id
    stars = message.successful_payment.total_amount
    charge_id = message.successful_payment.telegram_payment_charge_id
    await sub_manager.activate_subscription(user_id, stars, charge_id)
    await message.answer(
        f"✅ Abonelik aktif! {SUBSCRIPTION_DAYS} gün boyunca sınırsız indirme yapabilirsin.🎉"
    )


# ---------------------------------------------------------------------------
# callback: a:<cache_id>  -> bölüm listesi
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("a:"))
async def cb_anime(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("Hata: geçersiz veri")
        return
    cache_id = int(parts[1])
    slug = _anime_cache.get(cache_id)
    if not slug:
        await callback.answer("Arama sonucu süresi doldu, lütfen tekrar ara.", show_alert=True)
        return
    await callback.answer()

    msg = await callback.message.edit_text("📂 Anime bilgileri yükleniyor...")  # type: ignore[union-attr]

    try:
        info = await asyncio.to_thread(get_anime_info, slug)
        episodes = await asyncio.to_thread(get_episodes, slug)
    except Exception as e:
        logger.exception("Failed to load anime")
        await msg.edit_text("Anime bilgileri alınamadı. Lütfen tekrar deneyin.")
        return

    if not episodes:
        await msg.edit_text("Bu anime için bölüm bulunamadı.")
        return

    global _next_id
    ep_cache_id = _next_id
    _next_id += 1
    _episode_cache[ep_cache_id] = episodes

    header = f"<b>{info['title']}</b>\n{info['info'].get('Özet', '')[:200]}...\n\nBölümler:"
    kb = _make_episode_keyboard(ep_cache_id, episodes, 0)
    await msg.edit_text(header, reply_markup=kb, parse_mode="HTML")


# ---------------------------------------------------------------------------
# callback: p:<ep_cache_id>:<page_no>
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("p:"))
async def cb_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Hata: geçersiz veri")
        return
    ep_cache_id, page = int(parts[1]), int(parts[2])
    await callback.answer()

    episodes = _episode_cache.get(ep_cache_id)
    if not episodes:
        await callback.message.edit_text("Bölüm listesi süresi doldu, lütfen animeyi tekrar açın.")  # type: ignore[union-attr]
        return

    kb = _make_episode_keyboard(ep_cache_id, episodes, page)
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"Bölümler (sayfa {page + 1}):",
        reply_markup=kb,
    )


# ---------------------------------------------------------------------------
# callback: e:<ep_cache_id>:<ep_no>
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("e:"))
async def cb_episode(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Hata: geçersiz veri")
        return
    ep_cache_id, ep_no = int(parts[1]), int(parts[2])
    await callback.answer()

    user_id = callback.from_user.id
    ok, reason = await sub_manager.can_download(user_id)
    if not ok:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"💳 {SUBSCRIPTION_PRICE_STARS} ⭐ ile Abone Ol", callback_data="subscribe")]
            ]
        )
        await callback.message.edit_text(reason, reply_markup=kb)
        return

    episodes = _episode_cache.get(ep_cache_id)
    if not episodes:
        await callback.answer("Bölüm listesi süresi doldu, lütfen animeyi tekrar açın.", show_alert=True)
        return

    ep = next((e for e in episodes if e["no"] == ep_no), None)
    if ep is None:
        await callback.message.edit_text("Bölüm bulunamadı.")
        return

    msg: Message = callback.message
    status_msg = await msg.edit_text(f"⏳ Bölüm {ep_no} hazırlanıyor...")

    anime_slug = ep["_anime_slug"]
    episode_slug = ep["slug"]
    cache_key = cache.make_key(anime_slug, ep_no)

    tg_entry = await tg_cache.get(cache_key)
    if tg_entry:
        await status_msg.edit_text(f"📤 Telegram önbellekten gönderiliyor: Bölüm {ep_no}...")
        await bot.forward_message(
            chat_id=user_id,
            from_chat_id=tg_entry["chat_id"],
            message_id=tg_entry["message_id"],
        )
        await sub_manager.try_consume_download(user_id)
        return

    cached_path = await cache.get(cache_key)
    if cached_path:
        await status_msg.edit_text(f"📤 Disk önbellekten gönderiliyor: Bölüm {ep_no}...")
        await _send_video(msg, cached_path, anime_slug, ep_no)
        await sub_manager.try_consume_download(user_id)
        return

    lock = await _get_download_lock(cache_key)
    async with lock:
        tg_entry = await tg_cache.get(cache_key)
        if tg_entry:
            await status_msg.edit_text(f"📤 Telegram önbellekten gönderiliyor: Bölüm {ep_no}...")
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=tg_entry["chat_id"],
                message_id=tg_entry["message_id"],
            )
            await sub_manager.try_consume_download(user_id)
            await _release_download_lock(cache_key)
            return

        cached_path = await cache.get(cache_key)
        if cached_path:
            await status_msg.edit_text(f"📤 Disk önbellekten gönderiliyor: Bölüm {ep_no}...")
            await _send_video(msg, cached_path, anime_slug, ep_no)
            await sub_manager.try_consume_download(user_id)
            await _release_download_lock(cache_key)
            return

        async with download_semaphore:
            success = await _do_download_and_send(status_msg, msg, anime_slug, ep_no, episode_slug, cache_key)
            if success:
                await sub_manager.try_consume_download(user_id)

    await _release_download_lock(cache_key)


# ---------------------------------------------------------------------------
# callback: s:<ep_cache_id>  -> sezon indir
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("s:"))
async def cb_season(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("Hata: geçersiz veri")
        return
    ep_cache_id = int(parts[1])
    await callback.answer()

    user_id = callback.from_user.id
    ok, reason = await sub_manager.can_download(user_id)
    if not ok:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"💳 {SUBSCRIPTION_PRICE_STARS} ⭐ ile Abone Ol", callback_data="subscribe")]
            ]
        )
        await callback.message.edit_text(reason, reply_markup=kb)
        return

    episodes = _episode_cache.get(ep_cache_id)
    if not episodes:
        await callback.answer("Bölüm listesi süresi doldu, lütfen animeyi tekrar açın.", show_alert=True)
        return

    season_eps = [e for e in episodes if e["no"] <= SEASON_SIZE]
    if not season_eps:
        await callback.message.edit_text("Bu animede bölüm bulunamadı.")
        return

    anime_slug = season_eps[0]["_anime_slug"]
    msg = callback.message
    status_msg = await msg.edit_text(f"📦 {len(season_eps)} bölümlük sezon indiriliyor...")

    completed = 0
    failed = 0
    for ep in season_eps:
        ep_no = ep["no"]
        cache_key = cache.make_key(anime_slug, ep_no)
        lock = await _get_download_lock(cache_key)

        async with lock:
            tg_entry = await tg_cache.get(cache_key)
            if tg_entry:
                await status_msg.edit_text(f"📤 Önbellekten gönderiliyor: Bölüm {ep_no}/{SEASON_SIZE}...")
                try:
                    await bot.forward_message(
                        chat_id=user_id,
                        from_chat_id=tg_entry["chat_id"],
                        message_id=tg_entry["message_id"],
                    )
                    completed += 1
                    await sub_manager.try_consume_download(user_id)
                except Exception:
                    logger.warning(f"Season: forward failed for ep {ep_no}", exc_info=True)
                    failed += 1
                await _release_download_lock(cache_key)
                continue

            cached_path = await cache.get(cache_key)
            if cached_path:
                await status_msg.edit_text(f"📤 Diskten gönderiliyor: Bölüm {ep_no}...")
                try:
                    await _send_video(msg, cached_path, anime_slug, ep_no)
                    completed += 1
                    await sub_manager.try_consume_download(user_id)
                except Exception:
                    logger.warning(f"Season: send failed for ep {ep_no}", exc_info=True)
                    failed += 1
                await _release_download_lock(cache_key)
                continue

            async with download_semaphore:
                success = await _do_download_and_send(status_msg, msg, anime_slug, ep_no, ep["slug"], cache_key)
                if success:
                    completed += 1
                    await sub_manager.try_consume_download(user_id)
                else:
                    failed += 1

        await _release_download_lock(cache_key)

    await status_msg.edit_text(
        f"📦 Sezon indirme tamamlandı! {completed} başarılı, {failed} başarısız."
    )


async def _do_download_and_send(
    status_msg: Message,
    orig_msg: Message,
    slug: str,
    ep_no: int,
    episode_slug: str,
    cache_key: str,
) -> bool:
    _last_text: str = ""

    async def _edit(text: str) -> None:
        nonlocal _last_text
        if text != _last_text:
            await status_msg.edit_text(text)
            _last_text = text

    await _edit(f"🔍 Bölüm {ep_no} — video kaynakları aranıyor...")

    try:
        sources = await asyncio.wait_for(
            asyncio.to_thread(get_video_sources, episode_slug),
            timeout=DOWNLOAD_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Timeout getting video sources for {episode_slug}")
        await _edit(f"❌ Video kaynakları çözülemedi: zaman aşımı.")
        return False
    except Exception as e:
        logger.exception("Failed to get video sources")
        await _edit(f"❌ Video kaynakları çözülemedi: {e}")
        return False

    if not sources:
        await _edit("❌ Bu bölüm için çalışan video kaynağı bulunamadı.")
        return False

    total_sources = len(sources)
    await _edit(f"🎯 {total_sources} video kaynağı bulundu, deneniyor...")

    last_error = None
    for idx, src in enumerate(sources, 1):
        player = src["player"]
        await _edit(f"🔗 Kaynak çözülüyor ({idx}/{total_sources}) — {player}...")
        try:
            url = await asyncio.wait_for(
                asyncio.to_thread(resolve_video_url, src["video"]),
                timeout=30,
            )
        except (asyncio.TimeoutError, Exception):
            await _edit(f"⚠️ Kaynak atlandı ({idx}/{total_sources}) — {player} çözülemedi")
            continue
        if not url:
            await _edit(f"⚠️ Kaynak atlandı ({idx}/{total_sources}) — {player} URL yok")
            continue

        try:
            async def _progress(info: dict) -> None:
                if info["status"] == "downloading":
                    pct = info.get("percent", "?")
                    speed = info.get("speed")
                    speed_str = f" {_fmt_speed(speed)}" if speed else ""
                    eta = info.get("eta")
                    eta_str = f" — ⏱ {eta}s" if eta else ""
                    await _edit(
                        f"⬇️ İndiriliyor: %{pct}{speed_str}{eta_str}"
                    )
                elif info["status"] == "finished":
                    await _edit(f"⬇️ İndirme tamam, işleniyor...")

            await _edit(f"⬇️ İndiriliyor ({idx}/{total_sources}) — {player}...")
            tmp_path = await asyncio.wait_for(
                download_video(url, progress_callback=_progress),
                timeout=DOWNLOAD_TIMEOUT,
            )
        except asyncio.TimeoutError:
            last_error = Exception("zaman aşımı")
            await _edit(f"⚠️ İndirme zaman aşımı ({idx}/{total_sources}) — {player}")
            continue
        except Exception as e:
            last_error = e
            logger.warning(f"Download failed for {player}: {e}")
            await _edit(f"⚠️ İndirme başarısız ({idx}/{total_sources}) — {player}")
            continue

        try:
            file_size_mb = os.path.getsize(tmp_path) / 1024**2
        except OSError:
            file_size_mb = 0
        try:
            await _edit(f"💾 Diske kaydediliyor ({file_size_mb:.0f}MB)...")
            dest_path = await cache.put(cache_key, tmp_path, filename=f"{cache_key}.mp4")
            await _edit(f"📤 Sana gönderiliyor ({file_size_mb:.0f}MB)...")

            uploaded_to_group = False
            if cache_group_id:
                try:
                    input_file = FSInputFile(dest_path)
                    sent = await bot.send_video(
                        chat_id=cache_group_id,
                        video=input_file,
                        caption=f"{slug} Bölüm {ep_no}",
                    )
                    if sent.video:
                        await tg_cache.put(
                            cache_key,
                            chat_id=cache_group_id,
                            message_id=sent.message_id,
                            file_id=sent.video.file_id,
                        )
                        uploaded_to_group = True
                except Exception:
                    logger.warning("Failed to send to cache group", exc_info=True)

            await _send_video(orig_msg, dest_path, slug, ep_no)

            if uploaded_to_group:
                await cache.evict(cache_key)

            await _edit(f"✅ Bölüm {ep_no} gönderildi!")
            await asyncio.sleep(1)
            try:
                await status_msg.delete()
            except Exception:
                pass
            return True
        except Exception as e:
            last_error = e
            logger.exception(f"Failed to store/send downloaded episode {ep_no}")
            continue

    await _edit(
        f"❌ Bölüm {ep_no} indirilemedi. Tüm kaynaklar denendi: {last_error}"
    )
    return False


def _fmt_speed(speed) -> str:
    if speed is None:
        return ""
    if speed >= 1024**2:
        return f"{speed / 1024**2:.1f}MB/s"
    if speed >= 1024:
        return f"{speed / 1024:.0f}KB/s"
    return f"{speed}B/s"


async def _send_video(msg: Message, file_path: str, slug: str, ep_no: int) -> None:
    file_size = os.path.getsize(file_path)

    if file_size > config.MAX_TELEGRAM_FILE_SIZE:
        await msg.answer(
            f"⚠️ Dosya çok büyük ({file_size / 1024**2:.0f}MB). Telegram 50MB sınırı var. "
            "Yerel Bot API sunucusu kurmak için API_ID ve API_HASH gerekli.\n"
            "https://my.telegram.org/apps"
        )
        return

    try:
        input_file = FSInputFile(file_path)
        await msg.answer_video(input_file, caption=f"{slug} — Bölüm {ep_no}")
    except Exception as e:
        logger.exception("Failed to send file")
        await msg.answer(f"Dosya gönderilemedi: {e}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    global cache, sub_manager, tg_cache, preloader, cache_group_id

    logger.info("Starting bot...")
    pool = await create_pool(config.DATABASE_URL)

    cache = CacheManager(cache_dir=config.CACHE_DIR, max_bytes=config.MAX_CACHE_BYTES, pool=pool)
    await cache.init()
    sub_manager = SubscriptionManager(pool)
    await sub_manager.init()
    tg_cache = TelegramCache(pool)
    await tg_cache.init()

    v = await cache.get_config("cache_group_id")
    cache_group_id = int(v) if v is not None else None
    logger.info(f"Loaded cache_group_id from DB: {cache_group_id}")

    preloader = Preloader(
        pool=pool,
        download_semaphore=download_semaphore,
        cache_group_id_ref=lambda: cache_group_id,
        tg_cache=tg_cache,
        bot_ref=lambda: bot,
    )
    await preloader.init()
    await preloader.start()
    try:
        await dp.start_polling(bot)
    finally:
        await preloader.stop()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
