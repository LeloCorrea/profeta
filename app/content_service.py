def _sanitize_openai_text(text: str) -> str:
    if not text:
        return ""

    t = text.strip()

    # remove espaços iniciais
    t = t.lstrip()

    # remove TODOS os blocos markdown ```...``` (pega o primeiro válido)
    if "```" in t:
        parts = t.split("```")
        if len(parts) >= 3:
            t = parts[1].strip()

    # remove prefixos "json" com variações
    lower = t.lower()
    if lower.startswith("json"):
        t = t[4:]
        t = t.lstrip(": \n\r\t")

    return t.strip()
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
    # Compatível com OpenAI v1 (client.chat.completions.create)
    try:
        # Nova API: response.choices[0].message.content
        if hasattr(response, "choices") and response.choices:
            content = getattr(response.choices[0], "message", None)
            if content and hasattr(content, "content"):
                return str(content.content).strip()
            # fallback: pode ser só "content"
            if hasattr(response.choices[0], "content"):
                return str(response.choices[0].content).strip()
        # Legacy: output_text
        text = getattr(response, "output_text", None)
        if text:
            return str(text).strip()
        # Legacy: output -> content -> text
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


def validate_reflection_payload(payload: dict) -> tuple[bool, Optional[str]]:
    """
    Valida se o payload tem todos os campos obrigatórios e não vazios.
    """
    required = ["explanation", "application", "prayer"]
    for field in required:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            return False, f"Campo inválido: {field}"
    return True, None


def _fallback_reflection(verse: dict[str, Any], depth: str) -> ReflectionContent:
    reference = format_verse_reference(verse)
    explanation = (
        f"{reference} nos convida a contemplar esta Palavra com calma, recebendo sua mensagem "
        "como direção para o dia de hoje."
    )
    context = "Leia o texto novamente com atenção e observe o que ele revela sobre o caráter de Deus."
    application = "Escolha uma atitude prática para viver esta verdade ainda hoje, com simplicidade e constância."
    # Garante oração não vazia
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
        system_prompt = (
            "Você é um assistente espiritual cristão. Sempre responda SOMENTE com um objeto JSON válido, sem texto extra, sem markdown, sem explicações fora do JSON. "
            "O JSON deve conter os campos: explanation, application, prayer, context, summary. "
            "Seja simples, claro, fiel ao versículo, em português do Brasil. Não repita o versículo inteiro. Não invente doutrina. Não use markdown."
        )
        user_prompt = (
            f"Gere uma explicação espiritual para o versículo abaixo, respondendo apenas com um JSON válido e parseável, com os campos: explanation, application, prayer, context, summary.\n"
            f"Versículo: {verse['book']} {verse['chapter']}:{verse['verse']} - {verse['text']}"
        )
        logger.info("[IA] Gerando explicação via OpenAI para %s %s:%s", verse["book"], verse["chapter"], verse["verse"])
        response = client.chat.completions.create(
            model=OPENAI_EXPLANATION_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=512,
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
    logger.info("[IA] Resposta OpenAI (primeiros 200 chars): %s", text[:200] if text else "<vazio>")

    clean_text = _sanitize_openai_text(text or "")

    # Remove bloco markdown ```...```
    if clean_text.startswith("```"):
        parts = clean_text.split("```")
        if len(parts) >= 3:
            clean_text = parts[1].strip()

    # Remove prefixos comuns de JSON
    if clean_text.lower().startswith("json"):
        clean_text = clean_text[4:].lstrip(": \n\r\t")

    # Validação mínima segura
    if not clean_text or len(clean_text) < 20:
        logger.error("[IA] Resposta OpenAI inválida. Usando fallback.")
        fallback = _fallback_reflection(verse, depth)
        setattr(fallback, "is_fallback", True)
        return fallback

    # Evitar corte no meio de palavra
    if len(clean_text) > 120:
        preview = clean_text[:120].rsplit(" ", 1)[0]
    else:
        preview = clean_text

    log_event(
        logger,
        "reflection_generated",
        verse_reference=format_verse_reference(verse),
        depth=depth,
        journey_title=journey_title or "",
        source="openai",
    )
    return ReflectionContent(
        explanation=clean_text,
        context=f"Reflita sobre esta verdade: {preview}...",
        application="Como você pode aplicar isso hoje na sua vida de forma prática?",
        prayer="Senhor, aplica esta palavra no meu coração hoje. Amém.",
        is_fallback=False,
    )


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
    return _build_cached_reflection(verse, row.explanation or "", depth)


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
            if (
                getattr(row, "is_fallback", False)
                or (row.source and row.source != "openai")
                or not is_valid_explanation(row.explanation or "")
            ):
                logger.warning("Cache ignorado (inválido/legado)")
            else:
                logger.info("Usando cache de explicação para %s %s:%s", verse.get("book"), verse.get("chapter"), verse.get("verse"))
                return _build_cached_reflection(verse, row.explanation or "", depth)

    # Não encontrou cache válido, gera explicação
    reflection = await generate_reflection_content(
        verse,
        depth=depth,
        journey_title=journey_title,
    )
    # Salva no banco apenas se NÃO for fallback
    is_fallback = getattr(reflection, "is_fallback", False)
    source = "fallback" if is_fallback else "openai"
    # Busca ou cria usuário apenas para preencher user_id (não faz parte da chave do cache)
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
            logger.warning("[Fallback] Não persistindo reflexão fallback para %s %s:%s", verse.get("book"), verse.get("chapter"), verse.get("verse"))
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
    if getattr(reflection, "is_fallback", False):
        logger.warning("[Áudio] Bloqueado: fallback %s", reference)
        return ""
    return (
        f"Reflexão sobre {reference}. "
        f"Essência: {reflection.explanation} "
        f"Aplicação: {reflection.application} "
        f"Oração: {reflection.prayer}"
    )


def parse_reflection_text(text: str, depth: str) -> Optional[ReflectionContent]:
    if not text:
        return None

    cleaned_raw = clean_json_text(text)
    cleaned = extract_json(cleaned_raw)

    try:
        payload = json.loads(cleaned)
    except Exception as e:
        logger.error(f"[JSON] Erro ao fazer parsing: {e} | cleaned={cleaned!r}")
        return None

    valid, error = validate_reflection_payload(payload)
    if not valid:
        logger.error(f"[JSON] Payload inválido: {error} | payload={payload!r}")
        return None

    safe_payload = {
        "explanation": payload.get("explanation") or "",
        "application": payload.get("application") or "",
        "prayer": payload.get("prayer") or "",
    }

    return ReflectionContent.from_dict({**safe_payload, "depth": depth})


def is_valid_explanation(text: str) -> bool:
    if not text or len(text) < 100:
        return False
    blacklist = ["Essência:", "Contexto:", "Aplicação:", "Oração:"]
    if any(k.lower() in text.lower() for k in blacklist):
        return False
    return True
