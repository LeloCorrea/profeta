import os
from dotenv import load_dotenv

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "profeta")
ENV = os.getenv("ENV", "dev")

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
