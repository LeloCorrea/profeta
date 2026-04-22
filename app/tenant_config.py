import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


@dataclass
class TenantBranding:
    """Per-tenant copy and voice — core of white-label differentiation."""

    app_name: str = "Profeta"
    bot_description: str = "Palavra, reflexão e áudio — uma jornada espiritual guiada todos os dias."
    welcome_verse_ref: str = "Salmos 1:1-3"
    welcome_verse_text: str = (
        "Bem-aventurado o homem que não anda segundo o conselho dos ímpios..."
        " Antes, o seu prazer está na lei do Senhor, e na sua lei medita de dia e de noite."
        " Pois será como árvore plantada junto a ribeiros de águas, a qual dá o seu fruto no seu tempo;"
        " as suas folhas não cairão, e tudo quanto fizer prosperará."
    )
    subscription_pitch: str = (
        "Com o Profeta você recebe Palavra, reflexão profunda e áudio"
        " — uma jornada espiritual guiada todos os dias."
    )
    content_tone: str = "sereno, profundo e cuidadoso"
    payment_description: str = "Profeta - Acesso Mensal"


@dataclass
class TenantSecrets:
    """Credentials kept separate from operational metadata — vault migration path in Phase 2."""

    telegram_bot_token: str = ""
    asaas_api_key: str = ""
    asaas_webhook_token: str = ""
    # openai_api_key is intentionally absent: OpenAI is platform-centralized.
    # Per-tenant key is an Enterprise-tier feature (Phase 3+).


@dataclass
class TenantConfig:
    """
    Single source of truth for all tenant-specific configuration.

    Phase 1: populated via from_env() — identical to the current global config.
    Phase 2: populated from Control Plane DB, one instance per tenant process.
    """

    # ── Identity ────────────────────────────────────────────────────────────
    tenant_id: str = "profeta"
    tenant_slug: str = "profeta"

    # ── Runtime ─────────────────────────────────────────────────────────────
    env: str = "dev"

    # ── Bot ─────────────────────────────────────────────────────────────────
    bot_username: str = ""

    # ── Database ────────────────────────────────────────────────────────────
    database_url: str = ""

    # ── API ─────────────────────────────────────────────────────────────────
    public_base_url: str = ""

    # ── Asaas (non-sensitive metadata) ──────────────────────────────────────
    asaas_env: str = "sandbox"
    asaas_payment_link_id: str = ""
    asaas_payment_link_url: str = ""
    asaas_subscription_value: float = 29.90

    # ── OpenAI — platform-centralized ───────────────────────────────────────
    # openai_daily_token_cap: 0 = no cap; Phase 2 enforces quota per tenant.
    openai_model: str = "gpt-4o-mini"
    openai_daily_token_cap: int = 0
    default_explanation_depth: str = "balanced"

    # ── TTS ─────────────────────────────────────────────────────────────────
    tts_voice: str = "pt-BR-AntonioNeural"
    tts_rate: str = "-15%"

    # ── Feature flags ───────────────────────────────────────────────────────
    feature_inline_actions: bool = True
    feature_favorites: bool = True
    feature_journeys: bool = True
    feature_premium_prayer: bool = True

    # ── RBAC ────────────────────────────────────────────────────────────────
    # Phase 1: flat admin list.
    # Phase 2: roles — super_admin / tenant_owner / operator / support / financeiro.
    admin_telegram_ids: list[str] = field(default_factory=list)

    # ── Scheduling ──────────────────────────────────────────────────────────
    timezone: str = "America/Sao_Paulo"
    daily_send_hour: int = 8

    # ── Operational ─────────────────────────────────────────────────────────
    audio_max_age_days: int = 7
    rate_limit_versiculo: int = 10
    rate_limit_explicar: int = 5
    rate_limit_orar: int = 10

    # ── Secrets (separated for vault migration in Phase 2) ───────────────────
    secrets: TenantSecrets = field(default_factory=TenantSecrets)

    # ── Branding ────────────────────────────────────────────────────────────
    branding: TenantBranding = field(default_factory=TenantBranding)

    # ── Derived properties ───────────────────────────────────────────────────

    @property
    def asaas_base_url(self) -> str:
        if self.asaas_env.lower() in {"production", "prod"}:
            return "https://api.asaas.com/v3"
        return "https://sandbox.asaas.com/api/v3"

    def is_admin(self, telegram_user_id: str) -> bool:
        return bool(telegram_user_id) and telegram_user_id in self.admin_telegram_ids

    # ── Factory ─────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "TenantConfig":
        """Build TenantConfig from environment variables (Profeta as Tenant #1)."""
        project_root = Path(__file__).resolve().parents[1]
        default_db_path = (project_root / "data" / "profeta.db").resolve()
        default_db_url = f"sqlite+aiosqlite:///{default_db_path.as_posix()}"

        raw_ids = os.getenv("ADMIN_TELEGRAM_IDS", "")
        admin_ids = [uid.strip() for uid in raw_ids.split(",") if uid.strip()]

        return cls(
            tenant_id=os.getenv("TENANT_ID", "profeta"),
            tenant_slug=os.getenv("TENANT_SLUG", "profeta"),
            env=os.getenv("ENV", "dev"),
            bot_username=os.getenv("BOT_USERNAME", ""),
            database_url=os.getenv("DATABASE_URL", default_db_url),
            public_base_url=os.getenv("PUBLIC_BASE_URL", ""),
            asaas_env=os.getenv("ASAAS_ENV", "sandbox"),
            asaas_payment_link_id=os.getenv("ASAAS_PAYMENT_LINK_ID", ""),
            asaas_payment_link_url=os.getenv("ASAAS_PAYMENT_LINK_URL", ""),
            asaas_subscription_value=float(os.getenv("ASAAS_SUBSCRIPTION_VALUE", "29.90")),
            openai_model=os.getenv("OPENAI_EXPLANATION_MODEL", "gpt-4o-mini"),
            openai_daily_token_cap=_env_int("OPENAI_DAILY_TOKEN_CAP", 0),
            default_explanation_depth=os.getenv("DEFAULT_EXPLANATION_DEPTH", "balanced"),
            tts_voice=os.getenv("TTS_VOICE", "pt-BR-AntonioNeural"),
            tts_rate=os.getenv("TTS_RATE", "-15%"),
            feature_inline_actions=_env_bool("FEATURE_INLINE_ACTIONS", True),
            feature_favorites=_env_bool("FEATURE_FAVORITES", True),
            feature_journeys=_env_bool("FEATURE_JOURNEYS", True),
            feature_premium_prayer=_env_bool("FEATURE_PREMIUM_PRAYER", True),
            admin_telegram_ids=admin_ids,
            timezone=os.getenv("TIMEZONE", "America/Sao_Paulo"),
            daily_send_hour=_env_int("DAILY_SEND_HOUR", 8),
            audio_max_age_days=_env_int("AUDIO_MAX_AGE_DAYS", 7),
            rate_limit_versiculo=_env_int("RATE_LIMIT_VERSICULO", 10),
            rate_limit_explicar=_env_int("RATE_LIMIT_EXPLICAR", 5),
            rate_limit_orar=_env_int("RATE_LIMIT_ORAR", 10),
            secrets=TenantSecrets(
                telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                asaas_api_key=os.getenv("ASAAS_API_KEY", ""),
                asaas_webhook_token=os.getenv("ASAAS_WEBHOOK_TOKEN", ""),
            ),
            branding=TenantBranding(
                app_name=os.getenv("BRANDING_APP_NAME", "Profeta"),
                bot_description=os.getenv(
                    "BRANDING_BOT_DESCRIPTION",
                    "Palavra, reflexão e áudio — uma jornada espiritual guiada todos os dias.",
                ),
                welcome_verse_ref=os.getenv("BRANDING_WELCOME_VERSE_REF", "Salmos 1:1-3"),
                welcome_verse_text=os.getenv(
                    "BRANDING_WELCOME_VERSE_TEXT",
                    (
                        "Bem-aventurado o homem que não anda segundo o conselho dos ímpios..."
                        " Antes, o seu prazer está na lei do Senhor, e na sua lei medita de dia e de noite."
                        " Pois será como árvore plantada junto a ribeiros de águas,"
                        " a qual dá o seu fruto no seu tempo;"
                        " as suas folhas não cairão, e tudo quanto fizer prosperará."
                    ),
                ),
                subscription_pitch=os.getenv(
                    "BRANDING_SUBSCRIPTION_PITCH",
                    "Com o Profeta você recebe Palavra, reflexão profunda e áudio"
                    " — uma jornada espiritual guiada todos os dias.",
                ),
                content_tone=os.getenv("BRANDING_CONTENT_TONE", "sereno, profundo e cuidadoso"),
                payment_description=os.getenv("BRANDING_PAYMENT_DESCRIPTION", "Profeta - Acesso Mensal"),
            ),
        )
