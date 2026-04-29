from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from app.observability import get_logger, log_event

logger = get_logger(__name__)

# Trilha interna para versículos sem correspondência temática.
# Nunca aparece no teclado de seleção (não está em TRILHA_NAMES),
# não é exibida no texto do versículo (get_trilha_label retorna None),
# e não infla nenhuma trilha real com versículos sem tema definido.
INTERNAL_DEFAULT_TRILHA = "geral"

_TRILHA_KEYWORDS: dict[str, list[str]] = {
    "ansiedade": [
        "ansiedade", "ansioso", "aflição", "aflito", "preocupação", "preocupado",
        "temor", "medo", "paz de deus", "tranquilo", "repousar", "descanso",
        "quieto", "angústia", "angustiado", "turbado", "perturbado",
    ],
    "casamento": [
        "esposa", "esposo", "marido", "mulher", "casamento", "cônjuge",
        "aliança nupcial", "nupcial", "noiva", "noivo", "matrimônio",
        "unidos", "deixará pai e mãe",
    ],
    "direcao": [
        "caminho", "direção", "guiar", "conduzir", "veredas", "passos",
        "encaminhar", "direcionar", "guia", "trilha", "atalho", "vereda",
        "dirija", "conduz", "orienta", "orienta-me",
    ],
    "esperanca": [
        "esperança", "aguardar", "promessa", "futuro", "restaurar", "renovar",
        "aguardo", "esperar", "aguarda", "não desanimes", "não desista",
        "certeza", "virá", "será cumprido",
    ],
    "familia": [
        "filho", "filha", "pai", "mãe", "família", "lar", "descendentes",
        "geração", "filhos", "parentesco", "casa de", "herança", "pais",
        "crianças", "bênção sobre", "seus filhos",
    ],
    "fe": [
        "fé", "crer", "acreditar", "convicção", "firme na fé", "crença",
        "confiar em deus", "confia", "confiança", "crê", "sem fé",
        "pela fé", "certeza do que se espera",
    ],
    "forca": [
        "força", "coragem", "firmeza", "perseverar", "suportar", "resistir",
        "vencer", "fortalecer", "ânimo", "forte", "não temas", "não te assustes",
        "sê forte", "seja firme", "poderoso", "capacidade",
    ],
    "gratidao": [
        "gratidão", "ação de graças", "louvor", "agradecer", "bendizer",
        "glorificar", "louvai", "bendito", "graças", "render graças",
        "cantai", "celebrai", "louvemos",
    ],
    "perdao": [
        "perdão", "perdoar", "misericórdia", "reconciliação", "purificar",
        "absolver", "remir", "clemência", "remissão", "pecados perdoados",
        "não guarda rancor", "esquece as transgressões",
    ],
    "proposito": [
        "propósito", "chamado", "vocação", "missão", "planos", "obra",
        "serviço", "desígnio", "plano de deus", "para que", "designou",
        "escolhido", "enviado", "fui chamado",
    ],
    "sabedoria": [
        "sabedoria", "entendimento", "prudência", "discernimento", "instrução",
        "conhecimento", "sábio", "insensato", "tolo", "aprende", "ensina",
        "provérbio", "conselho", "corrija",
    ],
}

_VALID_KEYS: frozenset[str] = frozenset(_TRILHA_KEYWORDS.keys())


def _normalize_tags(tags: list[str]) -> list[str]:
    """Lowercase, strip, deduplicate preserving order, cap at 5."""
    seen: set[str] = set()
    result: list[str] = []
    for t in tags:
        if not isinstance(t, str):
            continue
        normalized = t.lower().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
        if len(result) == 5:
            break
    return result


def _validate_and_fix(result: ClassificationResult) -> ClassificationResult:
    """Ensure result is always in a consistent, saveable state."""
    valid_trilhas = _VALID_KEYS | {INTERNAL_DEFAULT_TRILHA}
    if not result.trilha or result.trilha not in valid_trilhas:
        result.trilha = INTERNAL_DEFAULT_TRILHA
    if not isinstance(result.tags, list):
        result.tags = []
    if not result.tags:
        result.tags = [result.trilha]
    result.confidence = float(min(max(result.confidence, 0.0), 1.0))
    return result


@dataclass
class ClassificationResult:
    trilha: str
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.5
    classified_by: str = "fallback"  # "ai" | "fallback"


def classify_verse_by_keywords(
    verse_text: str,
    exclude_trilhas: Optional[frozenset[str]] = None,
) -> ClassificationResult:
    """
    Heuristic keyword classification. Always returns a valid result.
    Zero-match verses are assigned INTERNAL_DEFAULT_TRILHA ("geral"),
    so no real user-visible trilha is inflated with unrelated content.
    Pass exclude_trilhas to skip over-represented categories.
    """
    text_lower = verse_text.lower()
    keyword_source = (
        {k: v for k, v in _TRILHA_KEYWORDS.items() if k not in exclude_trilhas}
        if exclude_trilhas
        else _TRILHA_KEYWORDS
    )
    if not keyword_source:
        return _validate_and_fix(
            ClassificationResult(trilha=INTERNAL_DEFAULT_TRILHA, tags=[], confidence=0.1, classified_by="fallback")
        )

    scores: dict[str, int] = {
        trilha: sum(1 for kw in kws if kw in text_lower)
        for trilha, kws in keyword_source.items()
    }
    best = max(scores, key=lambda k: scores[k])
    score = scores[best]

    if score == 0:
        return _validate_and_fix(
            ClassificationResult(trilha=INTERNAL_DEFAULT_TRILHA, tags=[], confidence=0.1, classified_by="fallback")
        )

    tags = _normalize_tags([kw for kw in _TRILHA_KEYWORDS[best] if kw in text_lower])
    confidence = min(0.4 + score * 0.15, 0.85)
    return _validate_and_fix(
        ClassificationResult(trilha=best, tags=tags, confidence=confidence, classified_by="fallback")
    )


async def _classify_with_ai_raw(verse_text: str, verse_ref: str) -> ClassificationResult:
    """
    Raw OpenAI classification — raises on any API failure.
    Falls back to keywords only when API key is absent.
    External callers should wrap with retry logic.
    """
    import os

    from openai import AsyncOpenAI

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return classify_verse_by_keywords(verse_text)

    from app.config import OPENAI_EXPLANATION_MODEL

    # Parse verse_ref ("Salmos 23:1", "1 Coríntios 13:4") into structured context.
    ref_parts = verse_ref.rsplit(" ", 1)
    book_part = ref_parts[0] if len(ref_parts) == 2 else verse_ref
    chapter_verse = ref_parts[1].split(":") if len(ref_parts) == 2 else ["", ""]
    chapter_part = chapter_verse[0]
    verse_part = chapter_verse[1] if len(chapter_verse) > 1 else ""

    user_message = (
        f"Livro: {book_part}\n"
        f"Capítulo: {chapter_part}\n"
        f"Versículo: {verse_part}\n"
        f"Texto: {verse_text}"
    )

    trilha_list = ", ".join(sorted(_VALID_KEYS))
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=OPENAI_EXPLANATION_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    'Classifique o versículo bíblico. Responda em JSON: '
                    '{"trilha": "<chave>", "tags": ["palavra1", "palavra2"], "confidence": 0.0}. '
                    f"Trilhas válidas: {trilha_list}. "
                    "Escolha a trilha que melhor representa o tema principal. "
                    "confidence deve ser 0.0 a 1.0 indicando sua certeza."
                ),
            },
            {"role": "user", "content": user_message},
        ],
        max_tokens=100,
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content.strip()
    data = json.loads(content)

    trilha = str(data.get("trilha", "")).strip().lower()
    if trilha not in _VALID_KEYS:
        # IA retornou chave inválida — usa heurística de palavras-chave.
        return classify_verse_by_keywords(verse_text)

    tags = _normalize_tags(data.get("tags", []))
    raw_confidence = data.get("confidence", 0.7)
    confidence = float(min(max(float(raw_confidence), 0.0), 1.0))

    return _validate_and_fix(
        ClassificationResult(trilha=trilha, tags=tags, confidence=confidence, classified_by="ai")
    )


async def classify_verse_with_ai(verse_text: str, verse_ref: str) -> ClassificationResult:
    """Classify via OpenAI with keyword fallback on any failure. Never raises."""
    try:
        return await _classify_with_ai_raw(verse_text, verse_ref)
    except Exception:
        logger.exception("Falha na classificação IA para %s — usando heurística", verse_ref)
        return classify_verse_by_keywords(verse_text)


async def classify_and_save_verse(verse_id: int, verse_text: str, verse_ref: str) -> Optional[str]:
    """Classifica e persiste todos os campos. Não-op se já classificado. Retorna trilha ou None."""
    result = await classify_verse_with_ai(verse_text, verse_ref)
    try:
        from datetime import datetime

        from app.db import SessionLocal
        from app.models import Verse as VerseModel

        async with SessionLocal() as session:
            obj = await session.get(VerseModel, verse_id)
            if obj and not obj.trilha:
                obj.trilha = result.trilha
                obj.tags = json.dumps(result.tags, ensure_ascii=False)
                obj.confidence = result.confidence
                obj.classified_at = datetime.utcnow()
                obj.classified_by = result.classified_by
                await session.commit()
        log_event(logger, "verse_classified", verse_id=verse_id, verse_ref=verse_ref, trilha=result.trilha)
    except Exception:
        logger.exception("Falha ao persistir trilha | verse_id=%s", verse_id)
    return result.trilha
