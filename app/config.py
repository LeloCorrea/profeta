import os
from dotenv import load_dotenv

load_dotenv()


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

APP_NAME = os.getenv("APP_NAME", "profeta")
ENV = os.getenv("ENV", "dev")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/profeta.db")

# 🔥 ASAAS
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY", "")
ASAAS_ENV = os.getenv("ASAAS_ENV", "sandbox")
ASAAS_WEBHOOK_SECRET = os.getenv("ASAAS_WEBHOOK_SECRET", "")
ASAAS_PAYMENT_LINK_ID = os.getenv("ASAAS_PAYMENT_LINK_ID", "")
ASAAS_PAYMENT_LINK_URL = os.getenv("ASAAS_PAYMENT_LINK_URL", "")

# 🔗 APP
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")
DAILY_SEND_HOUR = int(os.getenv("DAILY_SEND_HOUR", "8"))
OPENAI_EXPLANATION_MODEL = os.getenv("OPENAI_EXPLANATION_MODEL", "gpt-5.4")
DEFAULT_EXPLANATION_DEPTH = os.getenv("DEFAULT_EXPLANATION_DEPTH", "balanced")
TTS_VOICE = os.getenv("TTS_VOICE", "pt-BR-AntonioNeural")
TTS_RATE = os.getenv("TTS_RATE", "-15%")

# 🚩 FEATURE FLAGS
FEATURE_INLINE_ACTIONS = env_bool("FEATURE_INLINE_ACTIONS", True)
FEATURE_FAVORITES = env_bool("FEATURE_FAVORITES", True)
FEATURE_JOURNEYS = env_bool("FEATURE_JOURNEYS", True)
FEATURE_PREMIUM_PRAYER = env_bool("FEATURE_PREMIUM_PRAYER", True)
