import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        return int(raw_value)
    except ValueError as error:
        raise ValueError(f"Environment variable {name} must be an integer.") from error


def missing_settings(*names: str) -> list[str]:
    return [name for name in names if not os.getenv(name, "").strip()]


def is_production_environment() -> bool:
    return ENV.lower() in {"prod", "production"}

APP_NAME = os.getenv("APP_NAME", "profeta")
ENV = os.getenv("ENV", "dev")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

DEFAULT_DATABASE_PATH = (PROJECT_ROOT / "data" / "profeta.db").resolve()
DEFAULT_DATABASE_URL = f"sqlite+aiosqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

# 🔥 ASAAS
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY", "")
ASAAS_ENV = os.getenv("ASAAS_ENV", "sandbox")
ASAAS_WEBHOOK_TOKEN = os.getenv("ASAAS_WEBHOOK_TOKEN", "")
ASAAS_PAYMENT_LINK_ID = os.getenv("ASAAS_PAYMENT_LINK_ID", "")
ASAAS_PAYMENT_LINK_URL = os.getenv("ASAAS_PAYMENT_LINK_URL", "")
ASAAS_SUBSCRIPTION_VALUE = float(os.getenv("ASAAS_SUBSCRIPTION_VALUE", "29.90"))
ASAAS_BASE_URL = (
    "https://api.asaas.com/v3"
    if os.getenv("ASAAS_ENV", "sandbox").lower() in {"production", "prod"}
    else "https://sandbox.asaas.com/api/v3"
)

# 🔗 APP
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")
DAILY_SEND_HOUR = env_int("DAILY_SEND_HOUR", 8)
OPENAI_EXPLANATION_MODEL = os.getenv("OPENAI_EXPLANATION_MODEL", "gpt-4o-mini")
DEFAULT_EXPLANATION_DEPTH = os.getenv("DEFAULT_EXPLANATION_DEPTH", "balanced")
TTS_VOICE = os.getenv("TTS_VOICE", "pt-BR-AntonioNeural")
TTS_RATE = os.getenv("TTS_RATE", "-15%")

# 🚩 FEATURE FLAGS
FEATURE_INLINE_ACTIONS = env_bool("FEATURE_INLINE_ACTIONS", True)
FEATURE_FAVORITES = env_bool("FEATURE_FAVORITES", True)
FEATURE_JOURNEYS = env_bool("FEATURE_JOURNEYS", True)
FEATURE_PREMIUM_PRAYER = env_bool("FEATURE_PREMIUM_PRAYER", True)

# 🛡️ ADMIN
ADMIN_TELEGRAM_IDS = os.getenv("ADMIN_TELEGRAM_IDS", "")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

# ⏱️ RATE LIMITS (calls per hour per user)
RATE_LIMIT_VERSICULO = env_int("RATE_LIMIT_VERSICULO", 10)
RATE_LIMIT_EXPLICAR = env_int("RATE_LIMIT_EXPLICAR", 5)
RATE_LIMIT_ORAR = env_int("RATE_LIMIT_ORAR", 10)

# 🖼️ IMAGE REQUESTS
IMAGE_PRICE: float = float(os.getenv("IMAGE_PRICE", "3.90"))

CREDIT_PACKAGES: list[dict] = [
    {"credits": 1, "price": 5.00},
    {"credits": 3, "price": 10.00},
    {"credits": 7, "price": 20.00},
]

# 🧹 AUDIO CLEANUP
AUDIO_MAX_AGE_DAYS = env_int("AUDIO_MAX_AGE_DAYS", 7)
AUDIO_MIN_DISK_MB = env_int("AUDIO_MIN_DISK_MB", 100)


def is_admin(telegram_user_id: str) -> bool:
    if not ADMIN_TELEGRAM_IDS.strip():
        return False
    ids = {uid.strip() for uid in ADMIN_TELEGRAM_IDS.split(",") if uid.strip()}
    return telegram_user_id in ids


# ── Session backend ──────────────────────────────────────────────────────────
# Vazio = MemoryBackend (padrão). Configurado = RedisBackend com graceful fallback.
REDIS_URL = os.getenv("REDIS_URL", "")

# ── Tenant abstraction (Phase 1) ─────────────────────────────────────────────
# CURRENT_TENANT is the singleton for the current process.
# Phase 2: populated from Control Plane DB instead of env vars.
from app.tenant_config import TenantConfig  # noqa: E402

CURRENT_TENANT: TenantConfig = TenantConfig.from_env()
