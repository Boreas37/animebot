from typing import Any

import turkanime_api as ta


def search_anime(query: str) -> list[dict[str, str]]:
    results = ta.Anime.arama_yap(query)
    return [{"slug": slug, "title": title} for slug, title in results]


def get_anime_info(slug: str) -> dict[str, Any]:
    anime = ta.Anime(slug, parse_fansubs=False)
    anime.fetch_info()
    return {
        "slug": anime.slug,
        "title": anime.title,
        "image": anime.info.get("Resim", ""),
        "info": anime.info,
    }


def get_episodes(slug: str) -> list[dict[str, Any]]:
    anime = ta.Anime(slug, parse_fansubs=False)
    episodes = []
    ep_data = anime.get_bolum_listesi()
    bolumler = anime.bolumler
    for i, (ep_slug, ep_title) in enumerate(ep_data, start=1):
        ep_title_clean = ep_title if ep_title else f"Bölüm {i}"
        episodes.append({
            "no": i,
            "title": ep_title_clean,
            "slug": ep_slug,
            "_anime_slug": slug,
            "bolum": bolumler[i - 1] if i - 1 < len(bolumler) else None,
        })
    return episodes


def get_video_sources(episode_slug: str) -> list[dict[str, Any]]:
    bolum = ta.Bolum(episode_slug, parse_fansubs=False)
    return [
        {
            "player": v.player,
            "fansub": v.fansub or "",
            "video": v,
        }
        for v in bolum.videos
        if v.is_supported
    ]


def resolve_video_url(video_obj) -> str | None:
    try:
        url = video_obj.url
    except Exception:
        return None
    if url and "turkanime.tv/player/" in url:
        return None
    if url and ".turkanime.tv/player/" in url:
        return None
    return url
