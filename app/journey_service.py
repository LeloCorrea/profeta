from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.db_helpers import get_or_create_user_in_session
from app.models import User, UserJourney
from app.observability import get_logger, log_event


logger = get_logger(__name__)


@dataclass(frozen=True)
class JourneyDefinition:
    key: str
    title: str
    summary: str
    touchpoints: tuple[str, ...]


JOURNEYS: dict[str, JourneyDefinition] = {
    "ansiedade": JourneyDefinition(
        key="ansiedade",
        title="Ansiedade",
        summary="Respirar, confiar e reorganizar o coração diante de Deus.",
        touchpoints=(
            "Hoje, sua jornada começa com quietude: respire fundo, entregue o peso a Deus e permita que a Palavra organize seus pensamentos.",
            "Nesta etapa, observe o que precisa ser deixado nas mãos de Deus antes de buscar solução imediata.",
            "Siga com um gesto concreto de paz: desacelere, ore brevemente e releia o versículo que mais falou ao seu coração.",
        ),
    ),
    "esperanca": JourneyDefinition(
        key="esperanca",
        title="Esperança",
        summary="Reacender a confiança nas promessas de Deus mesmo em dias nublados.",
        touchpoints=(
            "Hoje, sua jornada pede olhos atentos para sinais de esperança que Deus já colocou no caminho.",
            "Nesta etapa, lembre-se: esperança cristã não é negação da dor, é permanência fiel no meio dela.",
            "Continue fortalecendo sua esperança com oração breve e uma decisão prática de perseverança.",
        ),
    ),
    "perdao": JourneyDefinition(
        key="perdao",
        title="Perdão",
        summary="Caminho de cura, reconciliação e libertação interior.",
        touchpoints=(
            "Comece reconhecendo o que ainda pesa no coração, sem endurecer a alma.",
            "Nesta etapa, peça a Deus coragem para soltar a dívida emocional que ainda aprisiona você.",
            "Siga em perdão como disciplina espiritual: um passo pequeno já é um avanço real.",
        ),
    ),
    "fe": JourneyDefinition(
        key="fe",
        title="Fé",
        summary="Fortalecer convicção, confiança e obediência no cotidiano.",
        touchpoints=(
            "Hoje, a fé é praticada em pequenos atos de confiança, não apenas em grandes declarações.",
            "Nesta etapa, deixe a Palavra ajustar sua visão antes de ajustar suas circunstâncias.",
            "Continue firmando a fé com uma resposta concreta de obediência ao que Deus já mostrou.",
        ),
    ),
    "proposito": JourneyDefinition(
        key="proposito",
        title="Propósito",
        summary="Discernir direção, vocação e serviço com sobriedade.",
        touchpoints=(
            "Sua jornada de propósito começa ouvindo antes de correr: clareza espiritual nasce em presença, não em pressa.",
            "Nesta etapa, pergunte a si mesmo onde Deus já lhe deu responsabilidade e oportunidade de servir.",
            "Continue seu propósito com fidelidade ao próximo passo, mesmo que ele pareça simples.",
        ),
    ),
    "forca": JourneyDefinition(
        key="forca",
        title="Força",
        summary="Receber firmeza interior para suportar e continuar.",
        touchpoints=(
            "Hoje, força espiritual significa permanecer de pé sem endurecer o coração.",
            "Nesta etapa, reconheça suas limitações e receba em oração a força que vem de Deus.",
            "Continue com coragem mansa: constância vale mais que impulsos intensos e curtos.",
        ),
    ),
    "familia": JourneyDefinition(
        key="familia",
        title="Família",
        summary="Cuidar de vínculos, escuta e reconciliação no lar.",
        touchpoints=(
            "Sua jornada pela família começa pelo cuidado com palavras e presença.",
            "Nesta etapa, escolha uma atitude de honra, serviço ou reconciliação dentro de casa.",
            "Continue pedindo a Deus sabedoria para sustentar paz e verdade no ambiente familiar.",
        ),
    ),
    "casamento": JourneyDefinition(
        key="casamento",
        title="Casamento",
        summary="Cultivar aliança, ternura e maturidade espiritual a dois.",
        touchpoints=(
            "Hoje, o casamento é cuidado com intencionalidade, paciência e verdade amorosa.",
            "Nesta etapa, reflita sobre como servir antes de exigir, ouvir antes de responder e honrar antes de corrigir.",
            "Continue fortalecendo sua aliança com oração, diálogo sereno e pequenas decisões de cuidado mútuo.",
        ),
    ),
}


def list_journeys() -> list[JourneyDefinition]:
    return list(JOURNEYS.values())


def get_journey(key: str) -> Optional[JourneyDefinition]:
    return JOURNEYS.get(key)


def build_journey_catalog_message(active_title: Optional[str] = None) -> str:
    active_line = f"Trilha ativa: {active_title}\n\n" if active_title else ""
    items = "\n".join(f"• {journey.title}: {journey.summary}" for journey in list_journeys())
    return f"🛤️ Trilhas do Profeta\n\n{active_line}{items}"


async def start_journey(session_factory, telegram_user_id: str, journey_key: str) -> Optional[JourneyDefinition]:
    journey = get_journey(journey_key)
    if not journey:
        return None

    async with session_factory() as session:
        user = await get_or_create_user_in_session(session, telegram_user_id)

        active_rows = (
            await session.execute(
                select(UserJourney).where(
                    UserJourney.user_id == user.id,
                    UserJourney.status == "active",
                )
            )
        ).scalars().all()

        for row in active_rows:
            row.status = "paused"

        session.add(
            UserJourney(
                user_id=user.id,
                journey_key=journey.key,
                status="active",
                current_step=0,
                last_touchpoint_at=datetime.utcnow(),
            )
        )
        await session.commit()

    log_event(logger, "journey_started", telegram_user_id=telegram_user_id, journey_key=journey_key)
    return journey


async def get_active_journey(session_factory, telegram_user_id: str) -> Optional[JourneyDefinition]:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
        if not user:
            return None

        row = await session.scalar(
            select(UserJourney).where(
                UserJourney.user_id == user.id,
                UserJourney.status == "active",
            )
        )
        if not row:
            return None

    return get_journey(row.journey_key)


async def build_active_journey_touchpoint(session_factory, telegram_user_id: str) -> Optional[str]:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
        if not user:
            return None

        row = await session.scalar(
            select(UserJourney).where(
                UserJourney.user_id == user.id,
                UserJourney.status == "active",
            )
        )
        if not row:
            return None

        journey = get_journey(row.journey_key)
        if not journey:
            return None

        step_index = row.current_step % len(journey.touchpoints)
        row.current_step += 1
        row.last_touchpoint_at = datetime.utcnow()
        await session.commit()

    log_event(
        logger,
        "journey_touchpoint_sent",
        telegram_user_id=telegram_user_id,
        journey_key=journey.key,
        step=step_index,
    )
    return f"🛤️ Trilha: {journey.title}\n\n{journey.touchpoints[step_index]}"