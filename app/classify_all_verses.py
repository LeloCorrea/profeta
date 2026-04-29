"""
Batch classification pipeline for all verses.

Usage:
    python -m app.classify_all_verses [options]

Flags:
    --limit N                      Max verses to process in this run
    --force-reclassify             Reclassify all verses (regardless of current state)
    --reclassify-low-confidence    Reclassify fallback-classified or low-confidence verses
    --dry-run                      Show what would happen without writing to DB
    --no-ai                        Use keyword fallback only (no OpenAI calls)
    --reset-checkpoint             Ignore saved checkpoint and start from the beginning
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select, or_

from app.db import SessionLocal
from app.models import Verse
from app.verse_classifier import (
    INTERNAL_DEFAULT_TRILHA,
    ClassificationResult,
    _classify_with_ai_raw,
    classify_verse_by_keywords,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_API_CALLS_PER_RUN = 200
BATCH_SIZE = 20
API_CALL_DELAY_S = 0.2          # 200ms between API calls
MAX_RETRIES = 3
FAILURE_RATE_THRESHOLD = 0.5    # disable AI if >50% of attempts fail
MIN_CALLS_FOR_RATE_CHECK = 5    # need at least this many attempts before checking
DISTRIBUTION_WARN_FRACTION = 0.4  # warn when a trilha exceeds 40% of fallback results
CONFIDENCE_THRESHOLD = 0.6      # below this → try to improve via AI or alt keywords
GERAL_FRACTION_THRESHOLD = 0.15  # warn when "geral" exceeds 15% of all results
MAX_QUALITY_SAMPLES = 20        # random samples logged at end for quality review

_CHECKPOINT_PATH = Path(__file__).resolve().parent.parent / ".classification_checkpoint"


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def _read_checkpoint() -> Optional[int]:
    """Return last processed verse id, or None if no checkpoint exists."""
    try:
        text = _CHECKPOINT_PATH.read_text(encoding="utf-8").strip()
        for line in text.splitlines():
            if line.startswith("last_processed_id="):
                return int(line.split("=", 1)[1].strip())
    except (FileNotFoundError, ValueError):
        pass
    return None


def _write_checkpoint(verse_id: int) -> None:
    try:
        _CHECKPOINT_PATH.write_text(f"last_processed_id={verse_id}\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("Falha ao salvar checkpoint: %s", exc)


def _clear_checkpoint() -> None:
    try:
        _CHECKPOINT_PATH.unlink(missing_ok=True)
    except Exception:
        pass


# ── Distribution helpers ──────────────────────────────────────────────────────

async def _get_distribution() -> dict[str, int]:
    """Return {trilha: count} for all classified verses, ordered by count desc."""
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Verse.trilha, func.count(Verse.id))
                .where(Verse.trilha.isnot(None))
                .group_by(Verse.trilha)
                .order_by(func.count(Verse.id).desc())
            )
        ).all()
    return {row[0]: row[1] for row in rows}


def _log_distribution(distribution: dict[str, int]) -> None:
    total = sum(distribution.values())
    logger.info("Distribuição por trilha:")
    for trilha, count in sorted(distribution.items(), key=lambda x: -x[1]):
        pct = (count / total * 100) if total else 0.0
        marker = " ⚠" if trilha == INTERNAL_DEFAULT_TRILHA else ""
        logger.info("  - %s: %d (%.1f%%)%s", trilha, count, pct, marker)


# ── Classification with retry ─────────────────────────────────────────────────

async def _classify_with_retry(
    verse_text: str,
    verse_ref: str,
    no_ai: bool,
) -> ClassificationResult:
    """Try AI with exponential backoff; fall back to keywords after max retries."""
    if no_ai:
        return classify_verse_by_keywords(verse_text)

    last_error: Optional[Exception] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await _classify_with_ai_raw(verse_text, verse_ref)
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                delay = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "Tentativa %d/%d falhou para %s — aguardando %ds. Erro: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    verse_ref,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

    logger.error(
        "Todas as %d tentativas falharam para %s — usando heurística. Erro: %s",
        MAX_RETRIES,
        verse_ref,
        last_error,
    )
    return classify_verse_by_keywords(verse_text)


# ── Post-classification improvement ──────────────────────────────────────────

async def _post_classify_improve(
    result: ClassificationResult,
    verse_text: str,
    verse_ref: str,
    use_ai: bool,
    overrep_trilhas: set[str],
) -> tuple[ClassificationResult, bool]:
    """Try to improve a weak classification result.

    Conditions that trigger improvement:
    - Result is INTERNAL_DEFAULT_TRILHA ("geral")
    - Confidence below threshold
    - Keyword result is in an over-represented trilha

    Strategy: AI retry first; then distribution-aware keyword for overrep case.
    Returns (improved_result, was_improved).
    """
    is_geral = result.trilha == INTERNAL_DEFAULT_TRILHA
    is_low_confidence = result.confidence < CONFIDENCE_THRESHOLD
    is_overrep = result.classified_by == "fallback" and result.trilha in overrep_trilhas

    if not (is_geral or is_low_confidence or is_overrep):
        return result, False

    # Try AI once
    if use_ai:
        try:
            ai_result = await _classify_with_ai_raw(verse_text, verse_ref)
            better = (
                (is_geral and ai_result.trilha != INTERNAL_DEFAULT_TRILHA)
                or (is_low_confidence and ai_result.confidence >= CONFIDENCE_THRESHOLD)
                or (is_overrep and ai_result.trilha not in overrep_trilhas)
            )
            if better:
                return ai_result, True
        except Exception:
            pass  # keep trying below

    # Distribution-aware keyword fallback for overrep case
    if is_overrep:
        alt = classify_verse_by_keywords(verse_text, exclude_trilhas=frozenset(overrep_trilhas))
        if alt.trilha != INTERNAL_DEFAULT_TRILHA and alt.trilha not in overrep_trilhas:
            return alt, True

    return result, False


# ── Persist ───────────────────────────────────────────────────────────────────

async def _persist_result(verse_id: int, result: ClassificationResult) -> None:
    async with SessionLocal() as session:
        obj = await session.get(Verse, verse_id)
        if obj:
            obj.trilha = result.trilha
            obj.tags = json.dumps(result.tags, ensure_ascii=False)
            obj.confidence = result.confidence
            obj.classified_at = datetime.utcnow()
            obj.classified_by = result.classified_by
            await session.commit()


# ── Main run ──────────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> None:
    run_start = time.monotonic()

    # ── Checkpoint ────────────────────────────────────────────────────────────
    checkpoint_id: Optional[int] = None
    if args.reset_checkpoint:
        _clear_checkpoint()
        logger.info("Checkpoint resetado.")
    else:
        checkpoint_id = _read_checkpoint()
        if checkpoint_id is not None:
            logger.info("Retomando do checkpoint: último id processado = %d", checkpoint_id)

    # ── Initial stats ─────────────────────────────────────────────────────────
    async with SessionLocal() as session:
        total_count = (
            await session.execute(select(func.count()).select_from(Verse))
        ).scalar_one()
        classified_count = (
            await session.execute(
                select(func.count()).select_from(Verse).where(Verse.trilha.isnot(None))
            )
        ).scalar_one()

    unclassified_count = total_count - classified_count
    logger.info("Processando %d versículos no banco", total_count)
    logger.info("%d já classificados", classified_count)
    logger.info("Classificando %d restantes", unclassified_count)

    # ── Fetch target verses ───────────────────────────────────────────────────
    async with SessionLocal() as session:
        stmt = select(Verse).order_by(Verse.id)
        if args.force_reclassify:
            pass  # process all verses
        elif args.reclassify_low_confidence:
            stmt = stmt.where(
                or_(
                    Verse.trilha.is_(None),
                    Verse.classified_by == "fallback",
                    Verse.confidence < CONFIDENCE_THRESHOLD,
                )
            )
        else:
            stmt = stmt.where(Verse.trilha.is_(None))

        if checkpoint_id is not None:
            stmt = stmt.where(Verse.id > checkpoint_id)
        if args.limit:
            stmt = stmt.limit(args.limit)
        verses = list((await session.execute(stmt)).scalars().all())

    to_process = len(verses)
    if to_process == 0:
        logger.info("Nenhum versículo para classificar. Encerrando.")
        await _log_final_distribution()
        return

    mode_label = " (DRY-RUN)" if args.dry_run else ""
    if args.reclassify_low_confidence:
        mode_label += " (RECLASSIFY-LOW-CONFIDENCE)"
    logger.info("Iniciando classificação de %d versículos%s", to_process, mode_label)

    # ── Process state ─────────────────────────────────────────────────────────
    classified_this_run = 0
    failed_count = 0
    geral_count = 0
    improved_count = 0

    # API call tracking for rate limit and failure-rate guard
    api_calls_used = 0
    api_limit_warned = False
    ai_attempted = 0
    ai_succeeded = 0
    ai_disabled = False

    # Fallback distribution tracking
    fallback_distribution: dict[str, int] = {}
    distribution_warned: set[str] = set()

    # Quality sample collection
    quality_samples: list[dict] = []

    # ── Loop ──────────────────────────────────────────────────────────────────
    for batch_start in range(0, to_process, BATCH_SIZE):
        batch = verses[batch_start : batch_start + BATCH_SIZE]

        for verse in batch:
            verse_ref = f"{verse.book} {verse.chapter}:{verse.verse}"

            # Determine whether to use AI for this verse
            api_limit_hit = not args.no_ai and api_calls_used >= MAX_API_CALLS_PER_RUN
            if api_limit_hit and not api_limit_warned:
                logger.warning(
                    "Limite de %d chamadas de API atingido — continuando com heurística.",
                    MAX_API_CALLS_PER_RUN,
                )
                api_limit_warned = True

            use_keyword_only = args.no_ai or api_limit_hit or ai_disabled

            try:
                result = await _classify_with_retry(
                    verse_text=verse.text,
                    verse_ref=verse_ref,
                    no_ai=use_keyword_only,
                )

                # Update API tracking from primary classification
                if not use_keyword_only:
                    ai_attempted += 1
                    if result.classified_by == "ai":
                        ai_succeeded += 1
                        api_calls_used += 1
                    elif ai_attempted >= MIN_CALLS_FOR_RATE_CHECK and not ai_disabled:
                        failure_rate = 1.0 - (ai_succeeded / ai_attempted)
                        if failure_rate > FAILURE_RATE_THRESHOLD:
                            ai_disabled = True
                            logger.warning(
                                "Taxa de falha da IA (%.0f%%) ultrapassou %d%% — desativando IA para esta execução.",
                                failure_rate * 100,
                                int(FAILURE_RATE_THRESHOLD * 100),
                            )

                # Post-classification improvement step
                use_ai_for_improvement = (
                    not use_keyword_only
                    and not ai_disabled
                    and api_calls_used < MAX_API_CALLS_PER_RUN
                )
                result, was_improved = await _post_classify_improve(
                    result,
                    verse_text=verse.text,
                    verse_ref=verse_ref,
                    use_ai=use_ai_for_improvement,
                    overrep_trilhas=distribution_warned,
                )
                if was_improved:
                    improved_count += 1

                # Track final result
                if result.trilha == INTERNAL_DEFAULT_TRILHA:
                    geral_count += 1

                # Track fallback distribution and warn once per over-represented trilha
                if result.classified_by == "fallback":
                    fallback_distribution[result.trilha] = (
                        fallback_distribution.get(result.trilha, 0) + 1
                    )
                    total_fallback = sum(fallback_distribution.values())
                    if total_fallback >= 20:
                        for trilha, count in fallback_distribution.items():
                            if (
                                trilha not in distribution_warned
                                and count / total_fallback > DISTRIBUTION_WARN_FRACTION
                            ):
                                distribution_warned.add(trilha)
                                logger.warning(
                                    "Distribuição desequilibrada: '%s' representa %.0f%% das classificações por heurística.",
                                    trilha,
                                    count / total_fallback * 100,
                                )

                # Collect quality sample
                quality_samples.append({
                    "ref": verse_ref,
                    "snippet": verse.text[:80],
                    "trilha": result.trilha,
                    "confidence": result.confidence,
                    "by": result.classified_by,
                })

                if args.dry_run:
                    logger.info(
                        "[DRY-RUN] %s → trilha=%s confidence=%.2f by=%s tags=%s",
                        verse_ref,
                        result.trilha,
                        result.confidence,
                        result.classified_by,
                        result.tags[:3],
                    )
                else:
                    await _persist_result(verse.id, result)
                    _write_checkpoint(verse.id)
                    logger.debug(
                        "%s → %s (%.2f, %s)",
                        verse_ref,
                        result.trilha,
                        result.confidence,
                        result.classified_by,
                    )

                classified_this_run += 1

            except Exception as exc:
                logger.error(
                    "Erro inesperado ao classificar verso %d (%s): %s",
                    verse.id,
                    verse_ref,
                    exc,
                )
                failed_count += 1
                continue

            if not use_keyword_only:
                await asyncio.sleep(API_CALL_DELAY_S)

    # Clear checkpoint when the full unclassified set is exhausted (no limit imposed)
    if not args.dry_run and not args.limit and to_process < BATCH_SIZE:
        _clear_checkpoint()

    # ── Summary ───────────────────────────────────────────────────────────────
    total_time = time.monotonic() - run_start
    avg_time = total_time / classified_this_run if classified_this_run else 0.0
    ai_pct = (ai_succeeded / classified_this_run * 100) if classified_this_run else 0.0
    fallback_pct = 100.0 - ai_pct
    geral_pct = (geral_count / classified_this_run * 100) if classified_this_run else 0.0

    logger.info("=" * 52)
    logger.info("Total de versículos no banco:  %d", total_count)
    logger.info("Já classificados (antes):      %d", classified_count)
    logger.info("Classificados nesta execução:  %d", classified_this_run)
    logger.info("Falharam:                      %d", failed_count)
    logger.info("Melhorados pós-reclassif.:     %d", improved_count)
    logger.info("Chamadas de API usadas:        %d", api_calls_used)
    logger.info("Tempo total:                   %.1fs", total_time)
    logger.info("Tempo médio por versículo:     %.2fs", avg_time)
    logger.info("IA usada:                      %.0f%%", ai_pct)
    logger.info("Fallback usado:                %.0f%%", fallback_pct)
    geral_warn = " ⚠ (> %.0f%%)" % (GERAL_FRACTION_THRESHOLD * 100) if geral_pct > GERAL_FRACTION_THRESHOLD * 100 else ""
    logger.info("Trilha 'geral' (interno):      %.0f%% (%d)%s", geral_pct, geral_count, geral_warn)
    if args.dry_run:
        logger.info("Modo simulação — nenhuma alteração foi salva no banco.")

    # ── Quality sample ────────────────────────────────────────────────────────
    if quality_samples:
        samples = random.sample(quality_samples, min(MAX_QUALITY_SAMPLES, len(quality_samples)))
        logger.info("─" * 52)
        logger.info("Amostra de qualidade (%d versículos)", len(samples))
        for s in samples:
            snippet = s["snippet"].replace("\n", " ")
            logger.info(
                "  %-22s → %-12s %.2f via %-8s | %s",
                s["ref"],
                s["trilha"],
                s["confidence"],
                s["by"],
                snippet[:60],
            )

    # ── Distribution report ───────────────────────────────────────────────────
    if not args.dry_run:
        distribution = await _get_distribution()
        _log_distribution(distribution)


async def _log_final_distribution() -> None:
    distribution = await _get_distribution()
    if distribution:
        _log_distribution(distribution)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classificação em lote de versículos da base de dados."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Número máximo de versículos a processar nesta execução.",
    )
    parser.add_argument(
        "--only-unclassified",
        action="store_true",
        default=True,
        help="Processar apenas versículos sem trilha (padrão).",
    )
    parser.add_argument(
        "--force-reclassify",
        action="store_true",
        default=False,
        help="Reclassificar todos os versículos, independentemente do estado atual.",
    )
    parser.add_argument(
        "--reclassify-low-confidence",
        action="store_true",
        default=False,
        help="Reclassificar versículos classificados por fallback ou com confidence < %.1f." % CONFIDENCE_THRESHOLD,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simular classificação sem salvar no banco.",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        default=False,
        help="Usar apenas classificador por palavras-chave (sem OpenAI).",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        default=False,
        help="Ignorar checkpoint salvo e iniciar do começo.",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
