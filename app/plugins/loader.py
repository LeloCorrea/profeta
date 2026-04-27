"""Plugin loader — carregamento dinâmico de plugins de jornadas.

Fase 4: importa plugins de `app.plugins.journeys.<key>.journey`.
         Plugins têm precedência sobre o dict hardcoded em journey_service.py.
         Se o plugin não existir ou falhar, journey_service faz fallback ao legado.

Convenção de plugin:
    app/plugins/journeys/<journey_key>/journey.py
    └─ deve exportar JOURNEY_DEFINITION: JourneyDefinition

Para adicionar um novo tenant-plugin no futuro:
    app/plugins/journeys/<tenant_id>/<journey_key>/journey.py
    (loader multi-tenant será adicionado na Fase 5+)
"""

import importlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def load_journey_plugin(journey_key: str) -> Optional[object]:
    """Carrega dinamicamente um plugin de jornada pelo key.

    Retorna o JourneyDefinition do plugin ou None se não encontrado/falhar.
    Nunca lança exceção — falhas são logadas e caller faz fallback ao legado.
    """
    module_path = f"app.plugins.journeys.{journey_key}.journey"
    try:
        module = importlib.import_module(module_path)
        definition = getattr(module, "JOURNEY_DEFINITION", None)
        if definition is None:
            logger.warning("Plugin %s não exporta JOURNEY_DEFINITION", module_path)
            return None
        return definition
    except ModuleNotFoundError:
        return None  # plugin não existe — silencioso, fallback ao legado
    except Exception as exc:
        logger.error("Erro ao carregar plugin %s: %s", module_path, exc)
        return None
