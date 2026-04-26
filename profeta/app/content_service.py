import asyncio
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

from sqlalchemy import or_, select

from app.config import CURRENT_TENANT, OPENAI_EXPLANATION_MODEL
from app.models import User, VerseExplanation
from app.observability import get_logger, log_event
from app.tenant_config import TenantConfig
from app.verse_service import format_verse_reference

try:
    from openai import OpenAI
    _openai_client: Optional[OpenAI] = None

    def _get_openai_client() -> OpenAI:
        global _openai_client
        if _openai_client is None:
            _openai_client = OpenAI()
        return _openai_client

except Exception:
    OpenAI = None
    _openai_client = None

    def _get_openai_client():  # type: ignore[misc]
        raise RuntimeError("Pacote openai não está disponível.")


logger = get_logger(__name__)
if __name__ == "app.content_service":
    if os.getenv("OPENAI_API_KEY"):
        logger.info("OPENAI_API_KEY carregada.")
    else:
        logger.warning("OPENAI_API_KEY NÃO encontrada! Geração de conteúdo não irá funcionar.")

# Per-verse generation locks: prevent duplicate OpenAI calls when two concurrent
# requests arrive for the same verse before the DB cache is populated.
_GENERATION_LOCKS: dict[str, asyncio.Lock] = {}


def _get_generation_lock(key: str) -> asyncio.Lock:
    if key not in _GENERATION_LOCKS:
        _GENERATION_LOCKS[key] = asyncio.Lock()
    return _GENERATION_LOCKS[key]


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ReflectionContent:
    explanation: str
    context: str
    application: str
    prayer: str
    summary: str
    depth: str = "balanced"
    is_fallback: bool = False

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReflectionContent":
        return cls(
            explanation=str(payload.get("explanation", "")).strip(),
            context=str(payload.get("context", "")).strip(),
            application=str(payload.get("application", "")).strip(),
            prayer=str(payload.get("prayer", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            depth=str(payload.get("depth", "balanced")).strip() or "balanced",
            is_fallback=bool(payload.get("is_fallback", False)),
        )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _sanitize_openai_text(text: str) -> str:
    if not text:
        return ""
    t = text.strip().lstrip()
    if "```" in t:
        parts = t.split("```")
        if len(parts) >= 3:
            t = parts[1].strip()
    lower = t.lower()
    if lower.startswith("json"):
        t = t[4:].lstrip(": \n\r\t")
    return t.strip()


def extract_response_text(response: Any) -> str:
    try:
        if hasattr(response, "choices") and response.choices:
            content = getattr(response.choices[0], "message", None)
            if content and hasattr(content, "content"):
                return str(content.content).strip()
            if hasattr(response.choices[0], "content"):
                return str(response.choices[0].content).strip()
        text = getattr(response, "output_text", None)
        if text:
            return str(text).strip()
        output = getattr(response, "output", None) or []
        parts: list[str] = []
        for item in output:
            content = getattr(item, "content", None) or []
            for block in content:
                value = getattr(block, "text", None)
                if value:
                    parts.append(str(value))
        return "\n".join(parts).strip()
    except Exception as e:
        logger.error(f"[IA] Erro ao extrair texto da resposta OpenAI: {e}")
        return ""


def build_default_prayer(verse: dict[str, Any]) -> str:
    reference = format_verse_reference(verse)
    return (
        f"Senhor, que a verdade de {reference} encontre espaço no meu coração hoje. "
        "Guia-me para viver conforme Tua vontade. Amém."
    )


def _fallback_reflection(verse: dict[str, Any], depth: str) -> ReflectionContent:
    reference = format_verse_reference(verse)

    explanation = (
        f"{reference} revela uma verdade espiritual importante. "
        "Mesmo quando não compreendemos todos os detalhes, Deus continua operando "
        "de forma soberana e fiel na história e em nossas vidas."
    )

    context = (
        "Observe o contexto deste versículo dentro da narrativa bíblica. "
        "Ele aponta para a fidelidade contínua de Deus ao Seu povo."
    )

    application = (
        "Hoje, escolha confiar em Deus mesmo sem entender tudo. "
        "Pratique uma atitude de fé simples e constante."
    )

    prayer = build_default_prayer(verse)

    summary = explanation

    return ReflectionContent(
        explanation=explanation,
        context=context,
        application=application,
        prayer=prayer,
        summary=summary,
        depth=depth,
        is_fallback=True,
    )

def _build_cached_reflection(verse: dict[str, Any], explanation: str, depth: str) -> ReflectionContent:
    reflection = _fallback_reflection(verse, depth)
    cleaned_explanation = explanation.strip()
    if cleaned_explanation:
        reflection.explanation = cleaned_explanation
        reflection.summary = cleaned_explanation
    reflection.is_fallback = False
    return reflection


def _build_prompts(verse: dict[str, Any], depth: str, journey_title: Optional[str]) -> tuple[str, str]:
    base_ref = f"{verse['book']} {verse['chapter']}:{verse['verse']} - {verse['text']}"
    journey_line = f" O usuário está na trilha espiritual '{journey_title}'." if journey_title else ""

    # 🔥 PROMPT BASE MAIS ROBUSTO (ANTI-QUEBRA)
    json_instruction = (
        "Responda EXCLUSIVAMENTE com JSON válido. "
        "Não inclua nenhum texto antes ou depois. "
        "Não use markdown. "
        "Não explique nada fora do JSON. "
        "Se cometer erro, corrija e retorne JSON válido. "
        "Formato obrigatório: "
        '{"explanation": "...", "application": "...", "prayer": "...", "context": "...", "summary": "..."}'
    )

    if depth == "deep":
        system_prompt = (
            "Você é um guia espiritual cristão profundo e contemplativo. "
            "Sua missão é tocar o coração com clareza, beleza e verdade bíblica. "
            + json_instruction + " "
            "Use linguagem rica, poética e espiritualmente madura, em português do Brasil. "
            "Inclua contexto histórico quando relevante. "
            "Evite generalizações vagas. Seja específico, profundo e fiel à Bíblia."
        )

        user_prompt = (
            "Gere uma reflexão espiritual profunda e transformadora baseada no versículo abaixo. "
            "A explanation deve ser contemplativa, com no mínimo 3 frases bem desenvolvidas. "
            "A application deve ser prática e direta para o dia a dia. "
            "A prayer deve ser sincera e conectada ao texto. "
            f"Versículo: {base_ref}{journey_line}"
        )

    else:
        system_prompt = (
            "Você é um assistente espiritual cristão claro e equilibrado. "
            "Sua missão é explicar a Palavra de forma compreensível e aplicável. "
            + json_instruction + " "
            "Seja direto, fiel ao texto bíblico e espiritualmente relevante. "
            "Evite linguagem genérica."
        )

        user_prompt = (
            "Explique o versículo abaixo de forma clara e prática. "
            "A explanation deve ser simples, mas significativa. "
            "A application deve ser objetiva e aplicável hoje. "
            "A prayer deve ser curta e coerente com o versículo. "
            f"Versículo: {base_ref}{journey_line}"
        )

    return system_prompt, user_prompt

# ── Core generator (private) ──────────────────────────────────────────────────

async def _generate_content(
    verse: dict[str, Any],
    depth: str,
    mode: str,
    journey_title: Optional[str],
    cfg: Optional[TenantConfig],
) -> ReflectionContent:
    """
    Unified OpenAI generator. `mode` is "explanation" or "reflection" and
    drives log event names and human-readable log messages.
    """
    _cfg = cfg or CURRENT_TENANT
    label = "reflexão" if mode == "reflection" else "explicação"
    start_event = f"{mode}_generation_started"
    done_event = f"{mode}_generated"

    def _run() -> str:
        client = _get_openai_client()
        system_prompt, user_prompt = _build_prompts(verse, depth, journey_title)
        max_tokens = 800 if depth == "deep" else 512

        logger.info(
            "[IA] Gerando %s via OpenAI para %s %s:%s",
            label, verse["book"], verse["chapter"], verse["verse"],
        )

        response = client.chat.completions.create(
            model=_cfg.openai_model or OPENAI_EXPLANATION_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_completion_tokens=max_tokens,  # ✅ já corrigido
            temperature=0.6 if depth == "deep" else 0.5,
        )

        return extract_response_text(response)

    log_event(
        logger,
        start_event,
        verse_reference=format_verse_reference(verse),
        journey_title=journey_title or "",
    )

    # ── A: API / operational error (auth, quota, timeout, network) ────────────
    # 🔥 CORRIGIDO: agora com retry sem quebrar a estrutura
    text = None
    #last_error = None

    for attempt in range(2):
        try:
            text = await asyncio.to_thread(_run)
            break
        except Exception as api_err:
            last_error = api_err

            log_event(
                logger,
                "openai_api_error",
                level=logging.ERROR if attempt == 1 else logging.WARNING,
                verse_reference=format_verse_reference(verse),
                mode=mode,
                attempt=attempt + 1,
                error_type=type(api_err).__name__,
                error=str(api_err)[:200],
            )

            if attempt == 0:
                await asyncio.sleep(0.5)
            else:
                return _fallback_reflection(verse, depth)

    logger.info(
        "[IA] Resposta OpenAI (primeiros 200 chars): %s",
        (text[:200] if isinstance(text, str) else "<vazio>")
    )

    clean_text = _sanitize_openai_text(text or "")

    # ── B: empty / too-short response (prompt or model issue) ────────────────
    if not clean_text or len(clean_text) < 20:
        log_event(
            logger,
            "openai_empty_response",
            level=logging.WARNING,
            verse_reference=format_verse_reference(verse),
            mode=mode,
            raw_length=len(text) if text else 0,
        )
        return _fallback_reflection(verse, depth)

    # ── C: JSON parse error (prompt engineering issue) ────────────────────────
    try:
        payload = json.loads(clean_text)

        reflection = ReflectionContent.from_dict({
            **payload,
            "depth": depth
        })

        log_event(
            logger,
            done_event,
            verse_reference=format_verse_reference(verse),
            journey_title=journey_title or "",
            source="openai",
        )

        return reflection

    except (json.JSONDecodeError, Exception) as parse_err:
        log_event(
            logger,
            "openai_parse_error",
            level=logging.WARNING,
            verse_reference=format_verse_reference(verse),
            mode=mode,
            error_type=type(parse_err).__name__,
            raw_excerpt=clean_text[:100],
        )

        return _fallback_reflection(verse, depth)

# ── Public generators ─────────────────────────────────────────────────────────

async def generate_explanation_content(
    verse: dict[str, Any],
    journey_title: Optional[str] = None,
    cfg: Optional[TenantConfig] = None,
) -> ReflectionContent:
    """Generate a balanced biblical explanation for /explicar."""
    return await _generate_content(verse, "balanced", "explanation", journey_title, cfg)


async def generate_reflection_content(
    verse: dict[str, Any],
    journey_title: Optional[str] = None,
    cfg: Optional[TenantConfig] = None,
) -> ReflectionContent:
    """Generate a deep spiritual reflection for /reflexao."""
    return await _generate_content(verse, "deep", "reflection", journey_title, cfg)


# ── Cache validation ──────────────────────────────────────────────────────────

def is_valid_explanation(text: str) -> bool:
    if not text or len(text) < 100:
        return False
    blacklist = ["Essência:", "Contexto:", "Aplicação:", "Oração:"]
    if any(k.lower() in text.lower() for k in blacklist):
        return False
    return True


# ── DB cache accessor (private) ───────────────────────────────────────────────

async def _get_or_create_content(
    session_factory,
    telegram_user_id: str,
    verse: dict[str, Any],
    *,
    depth: str,
    mode: str,
    journey_title: Optional[str],
) -> ReflectionContent:
    cache_event = f"{mode}_cached"
    generate_fn = generate_reflection_content if mode == "reflection" else generate_explanation_content

    lock_key = f"{verse['book']}:{verse['chapter']}:{verse['verse']}:{depth}"
    async with _get_generation_lock(lock_key):
        return await _get_or_create_content_locked(
            session_factory, telegram_user_id, verse,
            depth=depth, mode=mode, journey_title=journey_title,
            cache_event=cache_event, generate_fn=generate_fn,
        )


async def _get_or_create_content_locked(
    session_factory,
    telegram_user_id: str,
    verse: dict[str, Any],
    *,
    depth: str,
    mode: str,
    journey_title: Optional[str],
    cache_event: str,
    generate_fn,
) -> ReflectionContent:
    async with session_factory() as session:
        depth_filter = (
            or_(VerseExplanation.depth == "balanced", VerseExplanation.depth.is_(None))
            if depth == "balanced"
            else VerseExplanation.depth == depth
        )
        stmt = (
            select(VerseExplanation)
            .where(VerseExplanation.book == str(verse["book"]))
            .where(VerseExplanation.chapter == str(verse["chapter"]))
            .where(VerseExplanation.verse == str(verse["verse"]))
            .where(depth_filter)
            .order_by(VerseExplanation.created_at.asc())
            .limit(1)
        )
        row = await session.scalar(stmt)
        if row:
            if (
                getattr(row, "is_fallback", False)
                or (row.source and row.source != "openai")
                or not is_valid_explanation(row.explanation or "")
            ):
                logger.warning("Cache ignorado (inválido/legado)")
            else:
                logger.info(
                    "Usando cache de %s para %s %s:%s",
                    mode, verse.get("book"), verse.get("chapter"), verse.get("verse"),
                )
                return _build_cached_reflection(verse, row.explanation or "", depth)

    reflection = await generate_fn(verse, journey_title=journey_title)
    is_fallback = getattr(reflection, "is_fallback", False)
    source = "fallback" if is_fallback else "openai"

    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
        if not user:
            user = User(telegram_user_id=str(telegram_user_id), status="active")
            session.add(user)
            await session.flush()
        if not is_fallback or depth == "balanced":
            session.add(
                VerseExplanation(
                    user_id=user.id,
                    book=str(verse["book"]),
                    chapter=str(verse["chapter"]),
                    verse=str(verse["verse"]),
                    explanation=reflection.explanation.strip(),
                    source=source,
                    is_fallback=is_fallback, #is_fallback=False,
                    depth=depth,
                )
            )
            await session.commit()
            log_event(
                logger,
                cache_event,
                telegram_user_id=telegram_user_id,
                verse_reference=format_verse_reference(verse),
                source=source,
            )
        else:
            logger.warning(
                "[Fallback] Não persistindo %s fallback para %s %s:%s",
                mode, verse.get("book"), verse.get("chapter"), verse.get("verse"),
            )
    return reflection


# ── Public cache accessors ────────────────────────────────────────────────────

async def get_or_create_explanation_content(
    session_factory,
    telegram_user_id: str,
    verse: dict[str, Any],
    *,
    journey_title: Optional[str] = None,
) -> ReflectionContent:
    """Fetch or generate a balanced explanation for /explicar. Cache layer: 'explicacao'."""
    return await _get_or_create_content(
        session_factory,
        telegram_user_id,
        verse,
        depth="balanced",
        mode="explanation",
        journey_title=journey_title,
    )


async def get_or_create_reflection_content(
    session_factory,
    telegram_user_id: str,
    verse: dict[str, Any],
    *,
    journey_title: Optional[str] = None,
) -> ReflectionContent:
    """Fetch or generate a deep reflection for /reflexao. Cache layer: 'reflexao'."""
    return await _get_or_create_content(
        session_factory,
        telegram_user_id,
        verse,
        depth="deep",
        mode="reflection",
        journey_title=journey_title,
    )


# ── Render functions ──────────────────────────────────────────────────────────

def render_explanation_message(
    verse: dict[str, Any],
    reflection: ReflectionContent,
    journey_title: Optional[str] = None,
) -> str:
    journey_line = f"Trilha ativa: {journey_title}\n\n" if journey_title else ""
    ref = format_verse_reference(verse)
    return (
        f"📖 Explicação sobre {ref}\n\n"
        f"{journey_line}"
        f"{reflection.explanation}\n\n"
        f"📌 Contexto\n{reflection.context}\n\n"
        f"✅ Aplicação\n{reflection.application}"
    )


def render_reflection_message(
    verse: dict[str, Any],
    reflection: ReflectionContent,
    journey_title: Optional[str] = None,
) -> str:
    journey_line = f"Trilha ativa: {journey_title}\n\n" if journey_title else ""
    ref = format_verse_reference(verse)
    return (
        f"📖 Reflexão sobre {ref}\n\n"
        f"{journey_line}"
        f"✨ Essência\n{reflection.explanation}\n\n"
        f"🕊️ Contexto\n{reflection.context}\n\n"
        f"🌱 Aplicação\n{reflection.application}"
    )


def render_prayer_message(verse: dict[str, Any], prayer: str, journey_title: Optional[str] = None) -> str:
    journey_line = f"Trilha ativa: {journey_title}\n\n" if journey_title else ""
    return (
        f"🙏 Oração a partir de {format_verse_reference(verse)}\n\n"
        f"{journey_line}"
        f"{prayer}"
    )


# ── Audio text builders ───────────────────────────────────────────────────────

def tts_prepare(text: str) -> str:
    """Single TTS normalization gate — all audio routes must pass through here.

    Converts biblical X:Y references so TTS never reads them as a time.
    """
    return re.sub(r'\b(\d+):(\d+)\b', r'capítulo \1 versículo \2', text)


def build_explanation_audio_text(verse: dict[str, Any], reflection: ReflectionContent) -> str:
    """TTS script for /explicar. Returns '' for fallbacks to suppress audio.

    Sections mirror render_explanation_message (explanation + context + application).
    """
    reference = format_verse_reference(verse)
    if getattr(reflection, "is_fallback", False):
        logger.warning("[Áudio] fallback usado, mas áudio permitido")
    raw = (
        f"Explicação sobre {reference}. "
        f"{reflection.explanation} "
        f"Contexto: {reflection.context} "
        f"Aplicação: {reflection.application}"
    )
    return tts_prepare(raw)


def build_prayer_audio_text(verse: dict[str, Any], prayer: str) -> str:
    """TTS script for /orar."""
    reference = format_verse_reference(verse)
    return tts_prepare(f"Oração a partir de {reference}. {prayer}")


def build_reflection_audio_text(verse: dict[str, Any], reflection: ReflectionContent) -> str:
    """TTS script for /reflexao. Returns '' for fallbacks to suppress audio.

    Sections mirror render_reflection_message (essência + contexto + aplicação).
    """
    reference = format_verse_reference(verse)
    if getattr(reflection, "is_fallback", False):
        logger.warning("[Áudio] fallback usado, mas áudio permitido")
    raw = (
        f"Reflexão sobre {reference}. "
        f"Essência: {reflection.explanation} "
        f"Contexto: {reflection.context} "
        f"Aplicação: {reflection.application}"
    )
    return tts_prepare(raw)
