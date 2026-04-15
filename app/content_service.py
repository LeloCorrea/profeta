import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Any

from app.config import OPENAI_EXPLANATION_MODEL
from app.observability import get_logger, log_event
from app.verse_service import format_verse_reference

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


logger = get_logger(__name__)


@dataclass(slots=True)
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


def _build_prompt(verse: dict[str, Any], depth: str, journey_title: str | None = None) -> str:
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
    journey_title: str | None = None,
) -> ReflectionContent:
    if OpenAI is None:
        raise RuntimeError("Pacote openai não está disponível.")

    def _run() -> str:
        client = OpenAI()
        response = client.responses.create(
            model=OPENAI_EXPLANATION_MODEL,
            input=_build_prompt(verse, depth, journey_title),
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


def render_reflection_message(
    verse: dict[str, Any],
    reflection: ReflectionContent,
    journey_title: str | None = None,
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


def render_prayer_message(verse: dict[str, Any], prayer: str, journey_title: str | None = None) -> str:
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