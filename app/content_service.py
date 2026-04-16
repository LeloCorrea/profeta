import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Any, Optional

from sqlalchemy import select

from app.config import OPENAI_EXPLANATION_MODEL
import os
from app.models import User, VerseExplanation
from app.observability import get_logger, log_event
from app.verse_service import format_verse_reference

try:
    from openai import OpenAI
except Exception:
    OpenAI = None



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
        )


def extract_response_text(response: Any) -> str:
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


def build_default_prayer(verse: dict[str, Any]) -> str:
    reference = format_verse_reference(verse)
    return (
        f"Senhor, grava em meu coração a verdade de {reference}. "
        "Dá-me serenidade para obedecer à Tua voz, discernimento para viver esta Palavra "
        "e constância para permanecer perto de Ti hoje. Amém."
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
    return reflection


def _build_prompt(verse: dict[str, Any], depth: str, journey_title: Optional[str] = None) -> str:
    journey_context = ""
    if journey_title:
        journey_context = f"A pessoa está em uma jornada espiritual com foco em {journey_title}. "

    return (
        "Você é editor espiritual de um produto premium cristão no Telegram. "
        "Escreva em português do Brasil com linguagem serena, acolhedora, reverente e clara. "
        f"{journey_context}"
        "Responda somente em JSON válido com as chaves explanation, context, application, prayer e summary. "
        "Cada valor deve ser uma string. explanation, context e application devem ter no máximo 2 frases curtas. "
        "prayer deve ser uma oração breve baseada no versículo. summary deve ser uma síntese curta, boa para áudio. "
        f"Profundidade editorial desejada: {depth}. "
        f"Versículo: {verse['book']} {verse['chapter']}:{verse['verse']} - {verse['text']}"
    )


async def generate_reflection_content(
    verse: dict[str, Any],
    depth: str = "balanced",
    journey_title: Optional[str] = None,
) -> ReflectionContent:
    if OpenAI is None:
        raise RuntimeError("Pacote openai não está disponível.")

    def _run() -> str:
        client = OpenAI()
        prompt = (
            "Explique o versículo de forma simples, fiel ao texto bíblico, em português do Brasil, em no máximo 5 linhas. "
            "Não repita o versículo. Não adicione interpretações complexas."
        )
        logger.info("Gerando explicação via OpenAI (econômico) para %s %s:%s", verse["book"], verse["chapter"], verse["verse"])
        response = client.responses.create(
            model="gpt-5.4-mini",
            prompt=f"{prompt} Versículo: {verse['book']} {verse['chapter']}:{verse['verse']} - {verse['text']}",
            max_output_tokens=100,
            temperature=0.5,
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
    if not text:
        raise RuntimeError("A IA não retornou conteúdo de reflexão.")

    try:
        payload = json.loads(text)
        reflection = ReflectionContent.from_dict({**payload, "depth": depth})
    except json.JSONDecodeError:
        reflection = _fallback_reflection(verse, depth)
        reflection.explanation = text.strip() or reflection.explanation

    if not reflection.prayer:
        reflection.prayer = build_default_prayer(verse)
    if not reflection.summary:
        reflection.summary = f"{reflection.explanation} {reflection.application}".strip()

    log_event(
        logger,
        "reflection_generated",
        verse_reference=format_verse_reference(verse),
        depth=depth,
        journey_title=journey_title or "",
    )
    return reflection


async def get_cached_reflection_content(
    session_factory,
    verse: dict[str, Any],
    *,
    depth: str = "balanced",
) -> Optional[ReflectionContent]:
    async with session_factory() as session:
        # Busca cacheada global por versículo
        stmt = (
            select(VerseExplanation)
            .where(VerseExplanation.book == str(verse["book"]))
            .where(VerseExplanation.chapter == str(verse["chapter"]))
            .where(VerseExplanation.verse == str(verse["verse"]))
            .order_by(VerseExplanation.created_at.asc())
            .limit(1)
        )
        row = await session.scalar(stmt)

    if not row:
        log_event(
            logger,
            "reflection_cache_miss",
            book=verse.get("book"),
            chapter=verse.get("chapter"),
            verse=verse.get("verse"),
            depth=depth,
        )
        return None

    logger.info("Usando cache de explicação para %s %s:%s", verse.get("book"), verse.get("chapter"), verse.get("verse"))
    return _build_cached_reflection(verse, row.explanation, depth)


async def save_reflection_content(
    session_factory,
    telegram_user_id: str,
    verse: dict[str, Any],
    reflection: ReflectionContent,
) -> None:
    async with session_factory() as session:
        existing = await session.scalar(
            select(VerseExplanation)
            .where(VerseExplanation.book == str(verse["book"]))
            .where(VerseExplanation.chapter == str(verse["chapter"]))
            .where(VerseExplanation.verse == str(verse["verse"]))
            .order_by(VerseExplanation.created_at.asc())
            .limit(1)
        )
        if existing:
            log_event(
                logger,
                "reflection_cache_reused_before_save",
                telegram_user_id=telegram_user_id,
                verse_reference=format_verse_reference(verse),
            )
            return

        user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
        if not user:
            user = User(telegram_user_id=str(telegram_user_id), status="active")
            session.add(user)
            await session.flush()

        session.add(
            VerseExplanation(
                user_id=user.id,
                book=str(verse["book"]),
                chapter=str(verse["chapter"]),
                verse=str(verse["verse"]),
                explanation=reflection.explanation.strip(),
            )
        )
        await session.commit()

    log_event(
        logger,
        "reflection_cached",
        telegram_user_id=telegram_user_id,
        verse_reference=format_verse_reference(verse),
    )


async def get_or_create_reflection_content(
    session_factory,
    telegram_user_id: str,
    verse: dict[str, Any],
    *,
    depth: str = "balanced",
    journey_title: Optional[str] = None,
) -> ReflectionContent:
    # Busca cache global por versículo (book+chapter+verse)
    async with session_factory() as session:
        stmt = (
            select(VerseExplanation)
            .where(VerseExplanation.book == str(verse["book"]))
            .where(VerseExplanation.chapter == str(verse["chapter"]))
            .where(VerseExplanation.verse == str(verse["verse"]))
            .order_by(VerseExplanation.created_at.asc())
            .limit(1)
        )
        row = await session.scalar(stmt)
        if row:
            logger.info("Usando cache de explicação para %s %s:%s", verse.get("book"), verse.get("chapter"), verse.get("verse"))
            return _build_cached_reflection(verse, row.explanation, depth)

    # Não encontrou cache, gera explicação
    reflection = await generate_reflection_content(
        verse,
        depth=depth,
        journey_title=journey_title,
    )
    # Salva no banco (sem user_id na chave do cache)
    async with session_factory() as session:
        # Busca ou cria usuário apenas para preencher user_id (não faz parte da chave do cache)
        user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
        if not user:
            user = User(telegram_user_id=str(telegram_user_id), status="active")
            session.add(user)
            await session.flush()
        session.add(
            VerseExplanation(
                user_id=user.id,
                book=str(verse["book"]),
                chapter=str(verse["chapter"]),
                verse=str(verse["verse"]),
                explanation=reflection.explanation.strip(),
            )
        )
        await session.commit()
        log_event(
            logger,
            "reflection_cached",
            telegram_user_id=telegram_user_id,
            verse_reference=format_verse_reference(verse),
        )
    return reflection


def render_reflection_message(
    verse: dict[str, Any],
    reflection: ReflectionContent,
    journey_title: Optional[str] = None,
) -> str:
    journey_line = f"Trilha ativa: {journey_title}\n\n" if journey_title else ""
    return (
        f"📖 Reflexão sobre {format_verse_reference(verse)}\n\n"
        f"{journey_line}"
        f"✨ Essência\n{reflection.explanation}\n\n"
        f"🕊️ Contexto\n{reflection.context}\n\n"
        f"🌱 Aplicação\n{reflection.application}\n\n"
        f"🙏 Oração\n{reflection.prayer}"
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
    return (
        f"Reflexão sobre {reference}. "
        f"Essência: {reflection.explanation} "
        f"Aplicação: {reflection.application} "
        f"Oração: {reflection.prayer}"
    )
