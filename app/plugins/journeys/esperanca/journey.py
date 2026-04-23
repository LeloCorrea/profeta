"""Plugin: Trilha Esperança.

Primeiro plugin de jornada migrado do dict hardcoded em journey_service.py.
Estrutura idêntica à JourneyDefinition existente — compatível com o loader.

Para criar um novo plugin, copie este arquivo para:
    plugins/journeys/<key>/journey.py
e exporte JOURNEY_DEFINITION com os dados da trilha.
"""

from app.journey_service import JourneyDefinition

JOURNEY_DEFINITION = JourneyDefinition(
    key="esperanca",
    title="Esperança",
    summary="Reacender a confiança nas promessas de Deus mesmo em dias nublados.",
    touchpoints=(
        "Hoje, sua jornada pede olhos atentos para sinais de esperança que Deus já colocou no caminho.",
        "Nesta etapa, lembre-se: esperança cristã não é negação da dor, é permanência fiel no meio dela.",
        "Continue fortalecendo sua esperança com oração breve e uma decisão prática de perseverança.",
    ),
)
