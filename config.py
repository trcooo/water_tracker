import os

# ⚠️ Никогда не хардкодь токены в репозитории.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Публичный URL твоего проекта на Railway (обязательно https://)
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()

# Путь вебхука
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram/webhook").strip()

# Секрет для заголовка Telegram webhook (рекомендуется)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

# Fallback SQLite path (если не используешь Postgres)
DB_PATH = os.getenv("DB_PATH", "water.db").strip()

DEFAULT_ML_PER_KG = int(os.getenv("DEFAULT_ML_PER_KG", "33"))

if DEFAULT_ML_PER_KG < 30 or DEFAULT_ML_PER_KG > 35:
    raise RuntimeError("DEFAULT_ML_PER_KG должен быть 30..35")
