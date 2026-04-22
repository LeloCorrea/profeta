import asyncio
import json
import os
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
        logger.info("OPENAI_API_KEY carregada para geração de explicação.")
    else:
        logger.warning("OPENAI_API_KEY NÃO encontrada! Geração de explicação não irá funcionar.")


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
        t = t[4:]
        t = t.lstrip(": \n\r\t")

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
        f"{reference} nos convida a contemplar esta Palavra com calma, recebendo sua mensagem "
        "como direção para o dia de hoje."
    )
    context = "Leia o texto novamente com atenção e observe o que ele revela sobre o caráter de Deus."
    application = "Escolha uma atitude prática para viver esta verdade ainda hoje, com simplicidade e constância."
    prayer = build_default_prayer(verse)
    summary = f"{explanation} {application}"
    return ReflectionContent(
        explanation=explanation,
        context=context,
        application=application,
        prayer=prayer,
        summary=summary,
        depth=depth,
        is_fallback=True,
    )


def _build_cached_reflection(
    verse: dict[str, Any],
    explanation: str,
    depth: str,
) -> ReflectionContent:
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

    if depth == "deep":
        system_prompt = (
            "Você é um guia espiritual cristão de profundidade contemplativa. "
            "Sempre responda SOMENTE com um objeto JSON válido, sem texto extra, sem markdown. "
            "O JSON deve conter os campos: explanation, application, prayer, context, summary. "
            "Use linguagem rica, poética e teologicamente profunda, em português do Brasil. "
            "Inclua contexto histórico e cultural quando relevante. "
            "Toque a alma com contemplação genuína. Não invente doutrina. Não use markdown."
        )
        user_prompt = (
            "Gere uma reflexão espiritual profunda e contemplativa para o versículo abaixo. "
            "Responda apenas com um JSON válido com os campos: explanation, application, prayer, context, summary. "
            "A explanation deve ser rica e contemplativa (mínimo 3 frases). "
            f"Versículo: {base_ref}{journey_line}"
        )
    else:
        system_prompt = (
            "Você é um assistente espiritual cristão. Sempre responda SOMENTE com um objeto JSON válido, "
            "sem texto extra, sem markdown, sem explicações fora do JSON. "
            "O JSON deve conter os campos: explanation, application, prayer, context, summary. "
            "Seja simples, claro, fiel ao versículo, em português do Brasil. "
            "Não repita o versículo inteiro. Não invente doutrina. Não use markdown."
        )
        user_prompt = (
            "Gere uma explicação espiritual para o versículo abaixo, respondendo apenas com um JSON válido "
            f"e parseável, com os campos: explanation, application, prayer, context, summary.\n"
            f"Versículo: {base_ref}{journey_line}"
        )
    return system_prompt, user_prompt


async def generate_reflection_content(
    verse: dict[str, Any],
    depth: str = "balanced",
    journey_title: Optional[str] = None,
    cfg: Optional[TenantConfig] = None,
) -> ReflectionContent:
    _cfg = cfg or CURRENT_TENANT

    def _run() -> str:
        client = _get_openai_client()
        system_prompt, user_prompt = _build_prompts(verse, depth, journey_title)
        max_tokens = 800 if depth == "deep" else 512
        logger.info(
            "[IA] Gerando explicação via OpenAI para %s %s:%s (depth=%s)",
            verse["book"], verse["chapter"], verse["verse"], depth,
        )
        response = client.chat.completions.create(
            model=_cfg.openai_model or OPENAI_EXPLANATION_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.6 if depth == "deep" else 0.5,
        )
        return extract_response_text(response)

    log_event(
        logger,
        "reflection_generation_started",
        verse_reference=format_verse_reference(verse),
        depth=depth,
        journey_title=journey_title or "",
    )

    text = await asyncio.to_thread(_run)
    logger.info("[IA] Resposta OpenAI (primeiros 200 chars): %s", text[:200] if text else "<vazio>")

    clean_text = _sanitize_openai_text(text or "")

    if not clean_text or len(clean_text) < 20:
        logger.error("[IA] Resposta OpenAI inválida ou vazia. Usando fallback.")
        return _fallback_reflection(verse, depth)

    try:
        payload = json.loads(clean_text)
        reflection = ReflectionContent.from_dict({**payload, "depth": depth})
        log_event(
            logger,
            "reflection_generated",
            verse_reference=format_verse_reference(verse),
            depth=depth,
            journey_title=journey_title or "",
            source="openai",
        )
        return reflection
    except (json.JSONDecodeError, Exception) as e:
        logger.error("[IA] Erro ao parsear resposta OpenAI: %s | texto: %s", e, clean_text[:200])
        return _fallback_reflection(verse, depth)


def is_valid_explanation(text: str) -> bool:
    if not text or len(text) < 100:
        return False
    blacklist = ["Essência:", "Contexto:", "Aplicação:", "Oração:"]
    if any(k.lower() in text.lower() for k in blacklist):
        return False
    return True


async def get_or_create_reflection_content(
    session_factory,
    telegram_user_id: str,
    verse: dict[str, Any],
    *,
    depth: str = "balanced",
    journey_title: Optional[str] = None,
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
                    "Usando cache de explicação para %s %s:%s",
                    verse.get("book"), verse.get("chapter"), verse.get("verse"),
                )
                return _build_cached_reflection(verse, row.explanation or "", depth)

    reflection = await generate_reflection_content(
        verse,
        depth=depth,
        journey_title=journey_title,
    )
    is_fallback = getattr(reflection, "is_fallback", False)
    source = "fallback" if is_fallback else "openai"

    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
        if not user:
            user = User(telegram_user_id=str(telegram_user_id), status="active")
            session.add(user)
            await session.flush()
        if not is_fallback:
            session.add(
                VerseExplanation(
                    user_id=user.id,
                    book=str(verse["book"]),
                    chapter=str(verse["chapter"]),
                    verse=str(verse["verse"]),
                    explanation=reflection.explanation.strip(),
                    source=source,
                    is_fallback=False,
                    depth=depth,
                )
            )
            await session.commit()
            log_event(
                logger,
                "reflection_cached",
                telegram_user_id=telegram_user_id,
                verse_reference=format_verse_reference(verse),
                source=source,
            )
        else:
            logger.warning(
                "[Fallback] Não persistindo reflexão fallback para %s %s:%s",
                verse.get("book"), verse.get("chapter"), verse.get("verse"),
            )
    return reflection


def render_reflection_message(
    verse: dict[str, Any],
    reflection: ReflectionContent,
    journey_title: Optional[str] = None,
) -> str:
    journey_line = f"Trilha ativa: {journey_title}\n\n" if journey_title else ""
    ref = format_verse_reference(verse)

    if reflection.depth == "deep":
        return (
            f"📖 Reflexão sobre {ref}\n\n"
            f"{journey_line}"
            f"✨ Essência\n{reflection.explanation}\n\n"
            f"🕊️ Contexto\n{reflection.context}\n\n"
            f"🌱 Aplicação\n{reflection.application}"
        )
    return (
        f"📖 Explicação sobre {ref}\n\n"
        f"{journey_line}"
        f"{reflection.explanation}\n\n"
        f"📌 Contexto\n{reflection.context}\n\n"
        f"✅ Aplicação\n{reflection.application}"
    )


def render_prayer_message(verse: dict[str, Any], prayer: str, journey_title: Optional[str] = None) -> str:
    journey_line = f"Trilha ativa: {journey_title}\n\n" if journey_title else ""
    return (
        f"🙏 Oração a partir de {format_verse_reference(verse)}\n\n"
        f"{journey_line}"
        f"{prayer}"
    )


def build_explanation_audio_text(verse: dict[str, Any], reflection: ReflectionContent) -> str:
    reference = format_verse_reference(verse)
    if getattr(reflection, "is_fallback", False):
        logger.warning("[Áudio] Bloqueado: fallback %s", reference)
        return ""
    if reflection.depth == "deep":
        return (
            f"Reflexão sobre {reference}. "
            f"Essência: {reflection.explanation} "
            f"Aplicação: {reflection.application} "
            f"Oração: {reflection.prayer}"
        )
    return (
        f"Explicação sobre {reference}. "
        f"{reflection.explanation} "
        f"Aplicação: {reflection.application}"
    )
