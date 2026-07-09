# AnimeBot — Telegram Anime Download Bot

Telegram bot for searching and downloading anime episodes from [turkanime.net](https://turkanime.net). Features Postgres-backed caching, subscription management, Telegram Stars payments, and a local Bot API server for large file support (up to 2GB).

## Architecture

- **Python 3.11** + **aiogram 3.x** (async Telegram framework)
- **Postgres 15** via **asyncpg** (connection pooling, atomic operations)
- **yt-dlp** for video downloads
- **Docker** + **docker-compose** for deployment
- Optional local Telegram Bot API server (bypasses 50MB upload limit)

## Quick Start (Docker)

```bash
cp .env.example .env
# Edit .env with your BOT_TOKEN and Telegram API credentials
docker compose up -d
```

For large file support (>50MB), start with the local Bot API profile:

```bash
docker compose --profile local-bot-api up -d
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/ara <name>` | Search anime |
| `/abonelik` | Subscription status |
| `/stats` | Cache statistics |
| `/preload` | Preloader queue status |
| `/setcache` | Set current group as cache group (admin) |
| `/cachele <slug>` | Add anime to preload queue (admin) |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | yes | Telegram bot token from @BotFather |
| `DATABASE_URL` | yes | Postgres connection string |
| `POSTGRES_USER` | yes | Postgres user |
| `POSTGRES_PASSWORD` | yes | Postgres password |
| `POSTGRES_DB` | yes | Postgres database name |
| `TELEGRAM_API_ID` | local API | Telegram API ID from my.telegram.org |
| `TELEGRAM_API_HASH` | local API | Telegram API hash |
| `LOCAL_BOT_API_URL` | no | Local Bot API server URL |
| `ADMIN_IDS` | no | Comma-separated admin user IDs |
| `CACHE_DIR` | no | Disk cache path |
| `MAX_CACHE_BYTES` | no | Max cache size (default 5GB) |
| `MAX_CONCURRENT_DOWNLOADS` | no | Concurrent downloads (default 3) |
| `MAX_TELEGRAM_FILE_SIZE` | no | Max file size for uploads |
